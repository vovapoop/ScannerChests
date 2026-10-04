import os
import sys
import re
import subprocess
import hashlib


# ============================================================
# АВТОСОЗДАНИЕ И АВТОЗАПУСК ЧЕРЕЗ .venv
#
# При обычном запуске main.py программа сама:                 
# 1) создаёт .venv при его отсутствии;                    
# 2) устанавливает зависимости из requirements.txt;       
# 3) перезапускает main.py уже из созданного окружения.    
# ============================================================

_BASE_DIR_FOR_VENV = os.path.dirname(os.path.abspath(__file__))
_VENV_DIR = os.path.join(_BASE_DIR_FOR_VENV, ".venv")
_REQUIREMENTS_FILE = os.path.join(_BASE_DIR_FOR_VENV, "requirements.txt")
_REQUIREMENTS_HASH_FILE = os.path.join(_VENV_DIR, ".requirements.sha256")


def _venv_python_paths():
    if os.name == "nt":
        return (
            os.path.join(_VENV_DIR, "Scripts", "python.exe"),
            os.path.join(_VENV_DIR, "Scripts", "pythonw.exe"),
        )

    python = os.path.join(_VENV_DIR, "bin", "python")
    return python, python


def _venv_is_active():
    return os.path.abspath(sys.prefix) != os.path.abspath(sys.base_prefix)


def _requirements_hash():
    if not os.path.isfile(_REQUIREMENTS_FILE):
        return None
    digest = hashlib.sha256()
    with open(_REQUIREMENTS_FILE, "rb") as requirements_file:
        digest.update(requirements_file.read())
    return digest.hexdigest()


def _read_saved_requirements_hash():
    try:
        with open(_REQUIREMENTS_HASH_FILE, "r", encoding="utf-8") as hash_file:
            return hash_file.read().strip()
    except (OSError, UnicodeError):
        return None


def _write_saved_requirements_hash(value):
    os.makedirs(_VENV_DIR, exist_ok=True)
    with open(_REQUIREMENTS_HASH_FILE, "w", encoding="utf-8") as hash_file:
        hash_file.write(value)


def _run_setup_command(command):
    run_kwargs = {
        "cwd": _BASE_DIR_FOR_VENV,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }

    # Не показываем отдельное окно cmd.exe при создании .venv
    # и установке зависимостей через python.exe на Windows.
    if os.name == "nt":
        run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    result = subprocess.run(command, **run_kwargs)
    if result.returncode != 0:
        log_file = os.path.join(_BASE_DIR_FOR_VENV, "venv_setup.log")
        try:
            with open(log_file, "w", encoding="utf-8") as file:
                file.write(result.stdout or "")
        except OSError:
            pass
        raise RuntimeError(
            "Не удалось подготовить .venv. "
            f"Подробности записаны в: {log_file}"
        )


def _show_startup_error(error):
    log_file = os.path.join(_BASE_DIR_FOR_VENV, "startup_error.log")
    message = f"{type(error).__name__}: {error}"
    try:
        with open(log_file, "w", encoding="utf-8") as file:
            file.write(message)
    except OSError:
        pass

    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                message + f"\n\nПодробности: {log_file}",
                "Minecraft Chest Scanner — ошибка запуска",
                0x10,
            )
        except Exception:
            pass


def _ensure_venv_and_restart():
    active = _venv_is_active()
    venv_python, venv_pythonw = _venv_python_paths()

    # Если программа уже запущена из .venv, ничего дополнительно
    # не делаем. Установка зависимостей выполняется лаунчером
    # Запуск.vbs до старта main.py.
    if active:
        return

    setup_python = sys.executable
    if os.name == "nt" and os.path.basename(setup_python).lower() == "pythonw.exe":
        candidate = os.path.join(os.path.dirname(setup_python), "python.exe")
        if os.path.isfile(candidate):
            setup_python = candidate

    if not os.path.isfile(venv_python):
        _run_setup_command([setup_python, "-m", "venv", _VENV_DIR])

    if not os.path.isfile(venv_python):
        raise RuntimeError(".venv создана, но интерпретатор Python в ней не найден.")

    # Для Windows принципиально нужен pythonw.exe: не возвращаемся к
    # консольному python.exe, иначе снова появится окно CMD.
    if os.name == "nt" and not os.path.isfile(venv_pythonw):
        _run_setup_command([venv_python, "-m", "venv", "--clear", _VENV_DIR])

    if os.name == "nt" and not os.path.isfile(venv_pythonw):
        raise RuntimeError(".venv повреждена: не найден .venv\\Scripts\\pythonw.exe")

    # Резервный путь для запуска main.py напрямую, без Запуск.vbs.
    requirements_hash = _requirements_hash()
    if requirements_hash is not None and _read_saved_requirements_hash() != requirements_hash:
        _run_setup_command([
            venv_python,
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "-r",
            _REQUIREMENTS_FILE,
            "--disable-pip-version-check",
        ])
        _write_saved_requirements_hash(requirements_hash)

    # Windows: запускаем отдельный pythonw.exe без консольного окна.
    if os.name == "nt" and os.path.isfile(venv_pythonw):
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            [venv_pythonw, os.path.abspath(__file__), *sys.argv[1:]],
            cwd=_BASE_DIR_FOR_VENV,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
        if process.pid:
            sys.exit(0)
        raise RuntimeError("Не удалось запустить программу через pythonw.exe.")

    if active:
        return

    os.execv(venv_python, [venv_python, os.path.abspath(__file__), *sys.argv[1:]])


try:
    _ensure_venv_and_restart()
except Exception as startup_error:
    _show_startup_error(startup_error)
    raise

try:
    import cv2
    import json
    import time
    import uuid
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import keyboard
    import numpy as np
    import pyautogui
    import requests

    import winsound
    import threading
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
except Exception as import_error:
    _show_startup_error(import_error)
    raise

# Файл-маркер: программа действительно дошла до запуска основного кода.
_STARTUP_MARKER = os.path.join(_BASE_DIR_FOR_VENV, "scanner_running.txt")
try:
    with open(_STARTUP_MARKER, "w", encoding="utf-8") as marker_file:
        marker_file.write(
            f"PID: {os.getpid()}\n"
            f"Запущено: {datetime.now(ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y %H:%M:%S')}\n"
        )
except OSError:
    pass
from queue import Queue, Empty

# ============================================================
# ПУТИ
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ============================================================
# ПРОФИЛИ
#
# Каждый профиль — отдельная папка в profiles/<имя>/ со своими:
#   config.json, items.json, results/, unknown_items/,
#   discord_webhook.json.
# Активный профиль хранится в profiles/active_profile.txt.
# ============================================================

PROFILES_DIR = os.path.join(BASE_DIR, "profiles")
ACTIVE_PROFILE_FILE = os.path.join(PROFILES_DIR, "active_profile.txt")
DEFAULT_PROFILE_NAME = "default"

# Переменная окружения для временного выбора профиля (например, при
# тестировании или запуске ярлыка с конкретным профилем). Если она
# задана — файл profiles/active_profile.txt игнорируется.
PROFILE_ENV_VAR = "CHEST_PROFILE"


def _read_active_profile_name():
    """Читает имя активного профиля (или None)."""
    override = os.environ.get(PROFILE_ENV_VAR, "").strip()

    if override:
        sanitized = sanitize_profile_name(override)
        if sanitized:
            return sanitized

    try:
        with open(ACTIVE_PROFILE_FILE, "r", encoding="utf-8") as profile_file:
            name = profile_file.read().strip()
        return name or None
    except (OSError, UnicodeError):
        return None


def sanitize_profile_name(name):
    """Оставляет только безопасные символы в имени профиля."""
    allowed = "-_ ()[]"
    cleaned = "".join(
        ch for ch in (name or "").strip()
        if ch.isalnum() or ch in allowed
    ).strip()
    return cleaned[:40]


def get_profiles():
    """Список имён профилей (отсортированный, default — первый)."""
    if not os.path.isdir(PROFILES_DIR):
        return [DEFAULT_PROFILE_NAME]

    profiles = []

    for entry in sorted(os.listdir(PROFILES_DIR)):
        entry_path = os.path.join(PROFILES_DIR, entry)
        if os.path.isdir(entry_path):
            profiles.append(entry)

    if DEFAULT_PROFILE_NAME not in profiles:
        profiles.insert(0, DEFAULT_PROFILE_NAME)
    else:
        profiles.remove(DEFAULT_PROFILE_NAME)
        profiles.insert(0, DEFAULT_PROFILE_NAME)

    return profiles


def set_active_profile(name):
    """Сохраняет выбранный активный профиль."""
    os.makedirs(PROFILES_DIR, exist_ok=True)
    with open(ACTIVE_PROFILE_FILE, "w", encoding="utf-8") as profile_file:
        profile_file.write(name)


def migrate_legacy_data(profile_name):
    """
    Первый запуск системы профилей: данные из корня программы
    переносатся в профиль 'default' (копируются, оригиналы
    переименовываются с суффиксом .bak).
    """
    target_dir = os.path.join(PROFILES_DIR, profile_name)

    if os.path.exists(os.path.join(target_dir, "migrated.flag")):
        return False

    os.makedirs(target_dir, exist_ok=True)

    moved_any = False
    legacy_entries = ["config.json", "items.json", "results", "unknown_items"]

    for entry in legacy_entries:
        source = os.path.join(BASE_DIR, entry)
        destination = os.path.join(target_dir, entry)

        if not os.path.exists(source):
            continue

        if os.path.exists(destination):
            continue

        import shutil
        shutil.copytree(source, destination) if os.path.isdir(source) \
            else shutil.copy2(source, destination)

        try:
            os.rename(source, source + ".bak")
        except OSError:
            pass

        moved_any = True

    # Webhook тоже относится к профилю.
    webhook_source = os.path.join(BASE_DIR, "discord_webhook.json")
    webhook_target = os.path.join(target_dir, "discord_webhook.json")
    if os.path.isfile(webhook_source) and not os.path.exists(webhook_target):
        import shutil
        shutil.copy2(webhook_source, webhook_target)
        moved_any = True

    with open(os.path.join(target_dir, "migrated.flag"), "w", encoding="utf-8") as flag:
        flag.write("legacy data migrated here\n")

    return moved_any


ACTIVE_PROFILE = _read_active_profile_name()

if ACTIVE_PROFILE is None:
    # Профили ещё ни разу не создавались — переносим старые данные
    # в профиль по умолчанию и делаем его активным.
    migrate_legacy_data(DEFAULT_PROFILE_NAME)
    ACTIVE_PROFILE = DEFAULT_PROFILE_NAME
    set_active_profile(ACTIVE_PROFILE)

PROFILE_DIR = os.path.join(PROFILES_DIR, ACTIVE_PROFILE)
os.makedirs(PROFILE_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(PROFILE_DIR, "config.json")
ITEMS_FILE = os.path.join(PROFILE_DIR, "items.json")

ASSETS_DIR = os.path.join(BASE_DIR, "assets")
ITEMS_DIR = os.path.join(ASSETS_DIR, "items")
DIGITS_DIR = os.path.join(ASSETS_DIR, "digits")

RESULTS_DIR = os.path.join(PROFILE_DIR, "results")
UNKNOWN_DIR = os.path.join(PROFILE_DIR, "unknown_items")

RESULTS_FILE = os.path.join(RESULTS_DIR, "chests.json")
HISTORY_DIR = os.path.join(RESULTS_DIR, "history")
DISCORD_MESSAGE_IDS_FILE = os.path.join(RESULTS_DIR, "discord_message_ids.json")
DISCORD_WEBHOOK_FILE = os.path.join(PROFILE_DIR, "discord_webhook.json")
SETUP_DONE_FLAG = os.path.join(PROFILE_DIR, "setup_done.flag")


# ============================================================
# КОНФИГУРАЦИЯ ПО УМОЛЧАНИЮ
#
# Если config.json уже существует, его значения сохраняются.
# ============================================================

DEFAULT_CONFIG = {
    "reference_screen_size": [1920, 1080],

    "chest_template": "assets/chest_open.png",
    "empty_slot_template": "assets/empty_slot.png",

    # Область 54 ячеек большого сундука: 9 x 6.
    # Координаты нужно проверять горячей клавишей F7.
    "chest_grid": {
        "x": 798,
        "y": 351,
        "width": 324,
        "height": 216,
        "columns": 9,
        "rows": 6
    },

    # Иконка предмета внутри одного слота.
    "icon_roi_in_slot": [0.10, 0.08, 0.80, 0.67],

    # Область количества в правом нижнем углу слота.
    # Формат: [x, y, width, height].
    "count_roi_in_slot": [0.30, 0.40, 0.67, 0.58],

    "chest_template_threshold": 0.82,
    "empty_slot_threshold": 1200,
    "item_match_threshold": 2600,

    "scan_interval_seconds": 2,

    "discord_webhook_env": "DISCORD_WEBHOOK_URL",
    "discord_max_items_in_message": 25,

    # Формат сообщения Discord. Все эти параметры можно менять в config.json.
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
        "unknown_item": "• {name}"
    }
}


# ============================================================
# ОБЩИЕ ФУНКЦИИ
# ============================================================

def ensure_directories():
    """Создаёт папки программы, если их ещё нет."""
    os.makedirs(ASSETS_DIR, exist_ok=True)
    os.makedirs(ITEMS_DIR, exist_ok=True)
    os.makedirs(DIGITS_DIR, exist_ok=True)
    os.makedirs(PROFILES_DIR, exist_ok=True)
    os.makedirs(RESULTS_DIR, exist_ok=True)
    os.makedirs(HISTORY_DIR, exist_ok=True)
    os.makedirs(UNKNOWN_DIR, exist_ok=True)


# ============================================================
# ИМЕНА ФАЙЛОВ ПРЕДМЕТОВ
#
# При добавлении/переименовании предмета его фото автоматически
# переносится в assets/items/<slug>.png (или <slug>_2.png для
# второго шаблона того же предмета). Символы кириллицы
# транслитерируются, пробелы заменяются подчёркиваниями.
# ============================================================

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify_item_name(name):
    """'Ящик со свёклой' -> 'yaschko_so_svyokloy'-подобный слаг."""
    characters = []

    for character in (name or "").lower():
        if character in _TRANSLIT:
            characters.append(_TRANSLIT[character])
        elif character.isascii() and character.isalnum():
            characters.append(character)
        elif character in (" ", "-", "_"):
            characters.append("_")

    slug = "".join(characters).strip("_")
    slug = re.sub(r"_+", "_", slug)

    return slug or "item"


def item_images_of(item):
    """Возвращает список путей изображений предмета (1..N)."""
    images = item.get("images")

    if isinstance(images, list) and images:
        return [path for path in images if path]

    if item.get("image"):
        return [item["image"]]

    return []


def unique_slug_for_item(name, taken_slugs, preferred_ext=".png"):
    """Возвращает свободный слаг файла для имени предмета."""
    base_slug = slugify_item_name(name)
    stem = re.sub(r"\.\d+$", "", base_slug)  # не путать с суффиксом _2
    slug = stem
    counter = 2

    while slug.lower() in taken_slugs:
        slug = f"{stem}_{counter}"
        counter += 1

    taken_slugs.add(slug.lower())
    return slug


def rename_item_images(items_db, item, new_name):
    """
    Переименовывает все фото предмета под новое имя и обновляет
    записи в базе. Возвращает список новых относительных путей.
    """
    all_items = items_db.get("items", [])

    taken_slugs = set()
    for other in all_items:
        if other is item:
            continue
        for path in item_images_of(other):
            stem = os.path.splitext(os.path.basename(path))[0]
            stem = re.sub(r"_\d+$", "", stem)
            taken_slugs.add(stem.lower())

    old_paths = item_images_of(item)
    slug = unique_slug_for_item(new_name, taken_slugs)

    new_paths = []

    for index, old_relative in enumerate(old_paths):
        old_full = absolute_path(old_relative)

        if not os.path.isfile(old_full):
            # Файл потерян — оставляем ссылку как есть.
            new_paths.append(old_relative)
            continue

        extension = os.path.splitext(old_full)[1].lower() or ".png"
        suffix = "" if index == 0 else f"_{index + 1}"
        new_relative = f"assets/items/{slug}{suffix}{extension}"
        new_full = absolute_path(new_relative)

        if os.path.normpath(old_full) != os.path.normpath(new_full):
            os.makedirs(os.path.dirname(new_full), exist_ok=True)
            try:
                os.replace(old_full, new_full)
            except OSError as error:
                print(f"[БАЗА] Не удалось переименовать файл: {error}")
                new_paths.append(old_relative)
                continue

        new_paths.append(new_relative)

    item["images"] = new_paths
    item.pop("image", None)
    item["name"] = new_name

    return new_paths


def store_new_item_image(items_db, icon_image, name, source_path=None):
    """
    Кладёт изображение нового предмета в assets/items/<slug>.png.
    Если source_path задан (например, временный unknown-файл),
    файл переносится оттуда. Возвращает относительный путь.
    """
    all_items = items_db.get("items", [])

    taken_slugs = set()
    for other in all_items:
        for path in item_images_of(other):
            stem = os.path.splitext(os.path.basename(path))[0]
            stem = re.sub(r"_\d+$", "", stem)
            taken_slugs.add(stem.lower())

    slug = unique_slug_for_item(name, taken_slugs)
    relative_path = f"assets/items/{slug}.png"
    full_path = absolute_path(relative_path)

    os.makedirs(os.path.dirname(full_path), exist_ok=True)

    moved = False
    if source_path and os.path.isfile(source_path):
        try:
            os.replace(source_path, full_path)
            moved = True
        except OSError:
            moved = False

    if not moved and icon_image is not None and icon_image.size > 0:
        cv2.imwrite(full_path, icon_image)

    return relative_path


def next_image_slot_path(items_db, item):
    """Путь для следующего (дополнительного) фото предмета."""
    existing = item_images_of(item)
    slug = os.path.splitext(os.path.basename(existing[0]))[0] if existing \
        else "item"
    slug = re.sub(r"_\d+$", "", slug)
    index = len(existing) + 1
    extension = ".png"
    if existing:
        extension = os.path.splitext(existing[0])[1].lower() or ".png"
    return f"assets/items/{slug}_{index}{extension}"


def absolute_path(path):
    """Преобразует относительный путь в абсолютный."""
    if not path:
        return None

    if os.path.isabs(path):
        return path

    return os.path.join(BASE_DIR, path)


def deep_merge(default_data, user_data):
    """Объединяет стандартную и пользовательскую конфигурации."""
    result = default_data.copy()

    for key, value in user_data.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value

    return result


def load_json(path, default_data):
    """Безопасно загружает JSON."""
    if not os.path.exists(path):
        return default_data.copy()

    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)

    except (json.JSONDecodeError, OSError) as error:
        print(f"[JSON] Ошибка чтения {path}: {error}")
        return default_data.copy()


def save_json(path, data):
    """Сохраняет JSON."""
    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def screenshot_bgr():
    """Делает скриншот и возвращает BGR-изображение OpenCV."""
    screenshot = pyautogui.screenshot()
    screenshot_np = np.array(screenshot)

    return cv2.cvtColor(screenshot_np, cv2.COLOR_RGB2BGR)


def resize_image(image, width, height):
    """Изменяет размер изображения."""
    if image is None or image.size == 0:
        return None

    return cv2.resize(
        image,
        (max(1, int(width)), max(1, int(height))),
        interpolation=cv2.INTER_AREA
    )


def crop_relative(image, rect):
    """
    Вырезает относительную область изображения.

    rect = [x, y, width, height]
    Все значения от 0.0 до 1.0.
    """
    if image is None or image.size == 0:
        return None

    image_height, image_width = image.shape[:2]

    x1 = max(0, int(rect[0] * image_width))
    y1 = max(0, int(rect[1] * image_height))

    width = max(1, int(rect[2] * image_width))
    height = max(1, int(rect[3] * image_height))

    x2 = min(image_width, x1 + width)
    y2 = min(image_height, y1 + height)

    return image[y1:y2, x1:x2]


def image_distance(image_a, image_b):
    """
    Возвращает расстояние между предметами.
    Меньше = изображения больше похожи.
    """
    if image_a is None or image_b is None:
        return float("inf")

    if image_a.size == 0 or image_b.size == 0:
        return float("inf")

    image_a = resize_image(image_a, 48, 48)
    image_b = resize_image(image_b, 48, 48)

    gray_a = cv2.cvtColor(image_a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(image_b, cv2.COLOR_BGR2GRAY)

    mse = np.mean(
        (gray_a.astype(np.float32) - gray_b.astype(np.float32)) ** 2
    )

    hsv_a = cv2.cvtColor(image_a, cv2.COLOR_BGR2HSV)
    hsv_b = cv2.cvtColor(image_b, cv2.COLOR_BGR2HSV)

    hist_a = cv2.calcHist(
        [hsv_a], [0, 1], None,
        [16, 16],
        [0, 180, 0, 256]
    )

    hist_b = cv2.calcHist(
        [hsv_b], [0, 1], None,
        [16, 16],
        [0, 180, 0, 256]
    )

    cv2.normalize(hist_a, hist_a)
    cv2.normalize(hist_b, hist_b)

    correlation = cv2.compareHist(
        hist_a,
        hist_b,
        cv2.HISTCMP_CORREL
    )

    color_penalty = (
        1.0 - max(-1.0, min(1.0, correlation))
    ) * 1000

    return float(mse + color_penalty)

def split_discord_message(text, limit=1900):
    """
    Разбивает текст на части, каждая из которых меньше лимита Discord.
    Не допускает отправку сообщения длиннее 2000 символов.
    """
    parts = []
    current = ""

    for line in text.splitlines():
        line = line.rstrip()

        # Если отдельная строка сама длиннее лимита,
        # разрезаем её принудительно.
        while len(line) > limit:
            if current:
                parts.append(current.rstrip())
                current = ""

            parts.append(line[:limit])
            line = line[limit:]

        candidate = line

        if current:
            candidate = current + "\n" + line

        if len(candidate) > limit:
            if current:
                parts.append(current.rstrip())

            current = line
        else:
            current = candidate

    if current.strip():
        parts.append(current.rstrip())

    # Дополнительная защита от превышения лимита.
    safe_parts = []

    for part in parts:
        if len(part) <= limit:
            safe_parts.append(part)
        else:
            for position in range(0, len(part), limit):
                safe_parts.append(
                    part[position:position + limit]
                )

    return safe_parts

# ============================================================
# СКАНЕР СУНДУКОВ
# ============================================================

class ChestReader:
    def __init__(self):
        ensure_directories()

        self.active_profile = ACTIVE_PROFILE

        user_config = load_json(CONFIG_FILE, {})
        self.config = deep_merge(DEFAULT_CONFIG, user_config)
        save_json(CONFIG_FILE, self.config)

        self.items_db = load_json(ITEMS_FILE, {"items": []})

        if not isinstance(self.items_db, dict):
            self.items_db = {"items": []}

        if "items" not in self.items_db:
            self.items_db["items"] = []

        save_json(ITEMS_FILE, self.items_db)

        self.ref_w, self.ref_h = self.config["reference_screen_size"]
        self.grid = self.config["chest_grid"]

        self.chest_template = self.read_image(
            self.config["chest_template"]
        )

        self.empty_slot_template = self.read_image(
            self.config["empty_slot_template"]
        )

        self.digit_templates = self.load_digit_templates()

        self.last_chest_hash = None
        self.chest_was_open = False
        # Не спрашивать название одного и того же предмета повторно
        # в течение текущего запуска программы.
        self.prompted_unknowns = set()
        self.unknown_item_prompt = None

        self.print_startup_status()

    def clear_recognition_debug(self):
        debug_dir = os.path.join(
            RESULTS_DIR,
            "quantity_recognition_debug"
        )

        os.makedirs(debug_dir, exist_ok=True)

        for filename in os.listdir(debug_dir):
            if filename.lower().endswith(".png"):
                path = os.path.join(debug_dir, filename)

                try:
                    os.remove(path)
                except OSError:
                    pass

    def play_scan_start_sound(self):
        """Короткий сигнал в момент начала сканирования."""
        try:
            winsound.Beep(880, 120)
        except Exception as error:
            print(f"[ЗВУК] Не удалось воспроизвести стартовый сигнал: {error}")

    def play_success_sound(self):
        """Проигрывает звук после успешного сканирования."""
        try:
            winsound.Beep(880, 120)
            winsound.Beep(1175, 180)
        except Exception as error:
            print(f"[ЗВУК] Не удалось воспроизвести сигнал: {error}")

    def print_startup_status(self):
        print("\n===================================================")
        print("                 СКАНЕР СУНДУКОВ")
        print("===================================================")
        print(f"Папка: {BASE_DIR}")
        print(f"Разрешение: {self.ref_w} x {self.ref_h}")
        print(
            f"Ячеек сундука: "
            f"{self.grid['columns']} x {self.grid['rows']}"
        )
        print(f"Предметов в базе: {len(self.items_db['items'])}")
        print(f"Загружено цифр: {len(self.digit_templates)} / 10")

        if self.chest_template is None:
            print("\n[ВНИМАНИЕ] Не найден chest_open.png:")
            print(absolute_path(self.config["chest_template"]))

        if self.empty_slot_template is None:
            print("\n[ВНИМАНИЕ] Не найден empty_slot.png:")
            print(absolute_path(self.config["empty_slot_template"]))

        print("===================================================\n")

    def read_image(self, path):
        """Открывает изображение."""
        full_path = absolute_path(path)

        if not full_path or not os.path.exists(full_path):
            return None

        return cv2.imread(full_path, cv2.IMREAD_COLOR)

    # ========================================================
    # ЦИФРЫ
    # ========================================================

    def load_digit_templates(self):
        """
        Загружает:
        assets/digits/0.png
        ...
        assets/digits/9.png
        """
        templates = {}

        print(f"\nПоиск цифр в: {DIGITS_DIR}")

        for digit in range(10):
            path = os.path.join(DIGITS_DIR, f"{digit}.png")

            if not os.path.exists(path):
                print(f"[НЕТ] {digit}.png")
                continue

            image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)

            if image is None:
                print(f"[НЕ ОТКРЫТ] {digit}.png")
                continue

            prepared = self.prepare_digit(image)

            if prepared is None:
                print(f"[ПУСТАЯ ЦИФРА] {digit}.png")
                continue

            templates[str(digit)] = prepared
            print(f"[OK] {digit}.png")

        return templates

    @staticmethod
    def make_digit_mask(image):
        """
        Делает маску цифры:
        белое = цифра,
        чёрное = фон.

        Метод работает и для оригинального цветного изображения,
        и для уже бинарного PNG шаблона.
        """
        if image is None or image.size == 0:
            return None

        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image.copy()

        unique_values = np.unique(gray)

        # Если это уже почти чёрно-белая картинка.
        if len(unique_values) <= 3:
            return cv2.inRange(gray, 100, 255)

        # Использовать только очень светлые пиксели.
        # Увеличивайте 200 до 210/220, если в маску попадает иконка.
        mask = cv2.inRange(gray, 250, 255)


        # Убираем одиночный шум.
        # MORPH_CLOSE не применяется: он может склеить 6 и 4.
        kernel = np.ones((2, 2), np.uint8)

        mask = cv2.morphologyEx(
            mask,
            cv2.MORPH_OPEN,
            kernel,
            iterations=1
        )

        return mask

    @staticmethod
    def prepare_digit(image):
        """
        Обрезает реальную цифру и помещает её в поле 24x32
        без искажения пропорций.
        """
        if image is None or image.size == 0:
            return None

        binary = ChestReader.make_digit_mask(image)

        if binary is None:
            return None

        points = cv2.findNonZero(binary)

        if points is None:
            return None

        x, y, width, height = cv2.boundingRect(points)

        digit = binary[y:y + height, x:x + width]

        if digit.size == 0:
            return None

        canvas_width = 24
        canvas_height = 32
        padding = 2

        free_width = canvas_width - padding * 2
        free_height = canvas_height - padding * 2

        scale = min(
            free_width / max(1, width),
            free_height / max(1, height)
        )

        new_width = max(1, round(width * scale))
        new_height = max(1, round(height * scale))

        digit = cv2.resize(
            digit,
            (new_width, new_height),
            interpolation=cv2.INTER_NEAREST
        )

        canvas = np.zeros(
            (canvas_height, canvas_width),
            dtype=np.uint8
        )

        x_offset = (canvas_width - new_width) // 2
        y_offset = (canvas_height - new_height) // 2

        canvas[
            y_offset:y_offset + new_height,
            x_offset:x_offset + new_width
        ] = digit

        return canvas

    def match_digit(self, digit_image):
        """
        Сравнивает символ с цифрами 0-9.

        Возвращает:
        (найденная цифра, уверенность от 0.0 до 1.0)
        """
        if not self.digit_templates:
            return None, 0.0

        prepared = self.prepare_digit(digit_image)

        if prepared is None:
            return None, 0.0

        prepared_mask = prepared > 0

        best_digit = None
        best_score = -1.0

        for digit, template in self.digit_templates.items():
            if template is None:
                continue

            template_mask = template > 0

            # Проверяем небольшие сдвиги шаблона.
            for shift_y in range(-3, 4):
                for shift_x in range(-3, 4):
                    transform = np.float32([
                        [1, 0, shift_x],
                        [0, 1, shift_y]
                    ])

                    shifted = cv2.warpAffine(
                        template_mask.astype(np.uint8),
                        transform,
                        (
                            template_mask.shape[1],
                            template_mask.shape[0]
                        ),
                        flags=cv2.INTER_NEAREST,
                        borderValue=0
                    ).astype(bool)

                    intersection = np.logical_and(
                        prepared_mask,
                        shifted
                    ).sum()

                    union = np.logical_or(
                        prepared_mask,
                        shifted
                    ).sum()

                    if union == 0:
                        continue

                    iou = intersection / union

                    pixels_a = prepared_mask.sum()
                    pixels_b = shifted.sum()

                    size_score = 1.0 - (
                        abs(pixels_a - pixels_b)
                        / max(1, pixels_a, pixels_b)
                    )

                    score = iou * 0.80 + size_score * 0.20

                    if score > best_score:
                        best_score = score
                        best_digit = digit

        # Если цифра есть, но качество слабое, всё равно
        # возвращаем лучший вариант. Это полезно для отладки.
        if best_score < 0.10:
            return None, best_score

        return best_digit, best_score

    def read_quantity(self, slot_image, debug_name=None):
        """
        Читает число количества в ячейке.

        Если цифры не отображаются, Minecraft означает количество 1.
        """
        if not self.digit_templates:
            return 1

        count_image = crop_relative(
            slot_image,
            self.config["count_roi_in_slot"]
        )

        if count_image is None or count_image.size == 0:
            return 1

        binary = self.make_digit_mask(count_image)

        if binary is None:
            return 1

        image_height, image_width = binary.shape[:2]

        labels_count, _, stats, _ = cv2.connectedComponentsWithStats(
            binary,
            connectivity=8
        )

        characters = []

        for label in range(1, labels_count):
            x, y, width, height, area = stats[label]

            # Мелкий шум не является цифрой.
            if area < 2:
                continue

            if height < 3:
                continue

            # Слишком большой компонент — вероятно иконка предмета.
            if width > image_width * 0.80:
                continue

            if height > image_height * 0.95:
                continue

            characters.append({
                "x1": x,
                "y1": y,
                "x2": x + width,
                "y2": y + height
            })

        # Цифры отсутствуют: количество = 1.
        if not characters:
            return 1

        characters.sort(key=lambda item: item["x1"])

        result = []
        confidence_values = []

        # Отладочная картинка: белая маска + жёлтые рамки + красный ответ.
        debug_image = cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)

        for index, character in enumerate(characters):
            padding = 1

            x1 = max(0, character["x1"] - padding)
            y1 = max(0, character["y1"] - padding)
            x2 = min(image_width, character["x2"] + padding)
            y2 = min(image_height, character["y2"] + padding)

            digit_image = binary[y1:y2, x1:x2]

            digit, confidence = self.match_digit(digit_image)

            cv2.rectangle(
                debug_image,
                (x1, y1),
                (x2, y2),
                (0, 255, 255),
                1
            )

            if digit is not None:
                result.append(digit)
                confidence_values.append(confidence)

                cv2.putText(
                    debug_image,
                    digit,
                    (x1, max(8, y1 - 1)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.35,
                    (0, 0, 255),
                    1,
                    cv2.LINE_AA
                )
            else:
                print(
                    f"[ЦИФРА НЕ РАСПОЗНАНА] "
                    f"часть {index + 1}, "
                    f"уверенность: {confidence:.2f}"
                )

        # Сохраняем отладочную картинку последнего распознавания.
        debug_dir = os.path.join(
            RESULTS_DIR,
            "quantity_recognition_debug"
        )

        os.makedirs(debug_dir, exist_ok=True)

        if debug_name is None:
            debug_name = "last_recognition"

        debug_path = os.path.join(
            debug_dir,
            f"{debug_name}.png"
        )

        debug_large = cv2.resize(
            debug_image,
            None,
            fx=15,
            fy=15,
            interpolation=cv2.INTER_NEAREST
        )

        cv2.imwrite(debug_path, debug_large)

        number_text = "".join(result)

        if not number_text:
            return 1

        try:
            quantity = int(number_text)
        except ValueError:
            return 1

        if quantity < 1 or quantity > 9999:
            return 1

        confidence_text = ", ".join(
            f"{value:.2f}" for value in confidence_values
        )

        print(
            f"[КОЛИЧЕСТВО] {number_text} "
            f"| уверенность: {confidence_text}"
        )

        return quantity

    # ========================================================
    # СУНДУК И ЯЧЕЙКИ
    # ========================================================

    def chest_is_open(self, screen):
        """Проверяет, открыт ли большой сундук."""
        if self.chest_template is None:
            return False

        screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)

        template_gray = cv2.cvtColor(
            self.chest_template,
            cv2.COLOR_BGR2GRAY
        )

        template_h, template_w = template_gray.shape[:2]
        screen_h, screen_w = screen_gray.shape[:2]

        if template_h > screen_h or template_w > screen_w:
            return False

        result = cv2.matchTemplate(
            screen_gray,
            template_gray,
            cv2.TM_CCOEFF_NORMED
        )

        _, maximum, _, _ = cv2.minMaxLoc(result)

        return maximum >= self.config["chest_template_threshold"]

    def get_grid_rect(self, screen):
        """Масштабирует сетку под фактический размер экрана."""
        screen_h, screen_w = screen.shape[:2]

        scale_x = screen_w / self.ref_w
        scale_y = screen_h / self.ref_h

        return {
            "x": round(self.grid["x"] * scale_x),
            "y": round(self.grid["y"] * scale_y),
            "width": round(self.grid["width"] * scale_x),
            "height": round(self.grid["height"] * scale_y),
            "columns": self.grid["columns"],
            "rows": self.grid["rows"]
        }

    def get_slots(self, screen):
        """
        Вырезает ячейки сундука.
        Инвентарь игрока не включается.
        """
        grid = self.get_grid_rect(screen)

        slot_width = grid["width"] / grid["columns"]
        slot_height = grid["height"] / grid["rows"]

        slots = []

        for row in range(grid["rows"]):
            for column in range(grid["columns"]):
                x1 = round(grid["x"] + column * slot_width)
                x2 = round(grid["x"] + (column + 1) * slot_width)

                y1 = round(grid["y"] + row * slot_height)
                y2 = round(grid["y"] + (row + 1) * slot_height)

                # Убираем рамку слота Minecraft.
                padding = 1

                x1 += padding
                y1 += padding
                x2 -= padding
                y2 -= padding

                slot_image = screen[y1:y2, x1:x2]

                slots.append({
                    "row": row,
                    "column": column,
                    "image": slot_image,
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2
                })

        return slots

    def show_grid_debug(self):
        """
        F7: показывает красную общую рамку и зелёные рамки ячеек.
        """
        screen = screenshot_bgr()
        grid = self.get_grid_rect(screen)
        preview = screen.copy()

        cv2.rectangle(
            preview,
            (grid["x"], grid["y"]),
            (
                grid["x"] + grid["width"],
                grid["y"] + grid["height"]
            ),
            (0, 0, 255),
            2
        )

        slots = self.get_slots(screen)

        for index, slot in enumerate(slots, start=1):
            x1 = slot["x1"] - 1
            y1 = slot["y1"] - 1
            x2 = slot["x2"] + 1
            y2 = slot["y2"] + 1

            cv2.rectangle(
                preview,
                (x1, y1),
                (x2, y2),
                (0, 255, 0),
                1
            )

            cv2.putText(
                preview,
                str(index),
                (x1 + 3, y1 + 13),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.35,
                (0, 0, 255),
                1,
                cv2.LINE_AA
            )

        cv2.putText(
            preview,
            "RED = grid | GREEN = slots | Press any key to close",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 0, 255),
            2,
            cv2.LINE_AA
        )

        cv2.imshow("Chest grid calibration", preview)
        cv2.waitKey(0)
        cv2.destroyAllWindows()

    def save_quantity_debug(self):
        """
        F6: сохраняет область цифр всех 54 слотов.

        Папка:
        results/quantity_debug/
        """
        screen = screenshot_bgr()
        slots = self.get_slots(screen)

        debug_dir = os.path.join(
            RESULTS_DIR,
            "quantity_debug"
        )

        os.makedirs(debug_dir, exist_ok=True)

        for slot in slots:
            slot_image = slot["image"]

            if slot_image is None or slot_image.size == 0:
                continue

            count_image = crop_relative(
                slot_image,
                self.config["count_roi_in_slot"]
            )

            if count_image is None or count_image.size == 0:
                continue

            binary = self.make_digit_mask(count_image)

            row = slot["row"] + 1
            column = slot["column"] + 1

            original_path = os.path.join(
                debug_dir,
                f"slot_r{row}_c{column}_original.png"
            )

            binary_path = os.path.join(
                debug_dir,
                f"slot_r{row}_c{column}_binary.png"
            )

            original_large = cv2.resize(
                count_image,
                None,
                fx=15,
                fy=15,
                interpolation=cv2.INTER_NEAREST
            )

            binary_large = cv2.resize(
                binary,
                None,
                fx=15,
                fy=15,
                interpolation=cv2.INTER_NEAREST
            )

            cv2.imwrite(original_path, original_large)
            cv2.imwrite(binary_path, binary_large)

        print("\n[DEBUG] Области количества сохранены:")
        print(debug_dir)
        print("")

    # ========================================================
    # ПРЕДМЕТЫ
    # ========================================================

    def is_empty_slot(self, slot_image):
        """Проверяет пустоту слота."""
        if self.empty_slot_template is None:
            return False

        if slot_image is None or slot_image.size == 0:
            return True

        template = resize_image(
            self.empty_slot_template,
            slot_image.shape[1],
            slot_image.shape[0]
        )

        distance = image_distance(slot_image, template)

        return distance < self.config["empty_slot_threshold"]

    def extract_icon(self, slot_image):
        """Вырезает иконку предмета из ячейки."""
        return crop_relative(
            slot_image,
            self.config["icon_roi_in_slot"]
        )

    def detect_item(self, icon_image):
        """
        Ищет наиболее похожий предмет в items.json.

        Если предмет похож на несколько шаблонов почти одинаково
        или совпадение слишком слабое — он считается неизвестным.
        """
        candidates = []

        for item in self.items_db["items"]:
            item_name = item.get("name")

            if not item_name:
                continue

            # Поддержка одного шаблона:
            # "image": "assets/items/item.png"
            #
            # И нескольких шаблонов:
            # "images": ["...", "..."]
            image_paths = item.get("images")

            if not image_paths:
                image_paths = [item.get("image")]

            for image_path in image_paths:
                if not image_path:
                    continue

                template = self.read_image(image_path)

                if template is None:
                    continue

                template = resize_image(
                    template,
                    icon_image.shape[1],
                    icon_image.shape[0]
                )

                distance = image_distance(icon_image, template)

                candidates.append({
                    "name": item_name,
                    "image": image_path,
                    "distance": distance
                })

        # В базе вообще нет шаблонов.
        if not candidates:
            unknown_name = self.add_unknown_item(icon_image)
            return unknown_name, True, float("inf")

        # У одного предмета может быть несколько шаблонов.
        # Оставляем только лучший шаблон для каждого имени предмета.
        best_per_item = {}

        for candidate in candidates:
            name = candidate["name"]

            if (
                name not in best_per_item
                or candidate["distance"] < best_per_item[name]["distance"]
            ):
                best_per_item[name] = candidate

        sorted_candidates = sorted(
            best_per_item.values(),
            key=lambda candidate: candidate["distance"]
        )

        best = sorted_candidates[0]
        best_name = best["name"]
        best_distance = best["distance"]

        second_distance = float("inf")

        if len(sorted_candidates) > 1:
            second_distance = sorted_candidates[1]["distance"]

        threshold = self.config.get("item_match_threshold", 2600)
        margin = self.config.get("item_match_margin", 150)

        # Насколько лучший вариант лучше второго.
        difference = second_distance - best_distance

        if self.config.get("print_item_candidates", False):
            print(
                f"[ПРЕДМЕТ] Лучший: {best_name} "
                f"| distance={best_distance:.1f} "
                f"| второй={second_distance:.1f} "
                f"| разница={difference:.1f}"
            )

        # Шаблон слишком непохож на предмет в слоте.
        if best_distance > threshold:
            unknown_name = self.add_unknown_item(icon_image)

            print(
                f"[ПРЕДМЕТ] Слабое совпадение. "
                f"Создан неизвестный предмет: {unknown_name}"
            )

            return unknown_name, True, best_distance

        # Два разных предмета слишком похожи.
        # Лучше сохранить unknown, чем ошибочно назвать предмет.
        if second_distance != float("inf") and difference < margin:
            unknown_name = self.add_unknown_item(icon_image)

            print(
                f"[ПРЕДМЕТ] Неоднозначное совпадение: "
                f"{best_name}. Создан: {unknown_name}"
            )

            return unknown_name, True, best_distance

        # Если совпал старый неизвестный шаблон,
        # снова передаём его в add_unknown_item().
        # Функция сама проверит, было ли ему уже назначено название.
        if best_name.startswith("unknown_"):
            renamed_name = self.add_unknown_item(icon_image)

            return (
                renamed_name,
                renamed_name.startswith("unknown_"),
                best_distance
            )

        return best_name, False, best_distance

    def add_unknown_item(self, icon_image):
        """
        Сохраняет неизвестный предмет и спрашивает его название.

        Если пользователь нажмёт Enter без текста,
        предмет останется под именем unknown_XXXXXXXXXX.
        """
        if icon_image is None or icon_image.size == 0:
            return "unknown_empty"

        small_image = resize_image(icon_image, 32, 32)

        item_hash = hashlib.md5(
            small_image.tobytes()
        ).hexdigest()[:10]

        unknown_name = f"unknown_{item_hash}"
        relative_path = f"unknown_items/{unknown_name}.png"
        full_path = absolute_path(relative_path)

        existing_item = None

        # Ищем уже сохранённый предмет по имени или изображению.
        for item in self.items_db.get("items", []):
            item_name = item.get("name", "")
            item_images = item_images_of(item)

            normalized_full = os.path.normpath(full_path)

            matched_image = any(
                os.path.normpath(absolute_path(path)) == normalized_full
                for path in item_images
            ) or (
                # Предмет уже мог быть переименован в assets/items/<slug>.png.
                item_name == unknown_name
                or f"unknown_items/{unknown_name}.png" in item_images
            )

            if item_name == unknown_name or matched_image:
                existing_item = item
                break

        # Если предмет уже был переименован ранее,
        # сразу возвращаем сохранённое название.
        if existing_item is not None:
            existing_name = existing_item.get("name", unknown_name)

            if (
                existing_name != unknown_name
                and not existing_name.startswith("unknown_")
            ):
                return existing_name

        # Сохраняем изображение, если его ещё нет.
        if not os.path.exists(full_path):
            cv2.imwrite(full_path, icon_image)

        # Если этот предмет уже спрашивался в текущем запуске,
        # повторно вопрос не показываем.
        if unknown_name in self.prompted_unknowns:
            return (
                existing_item.get("name", unknown_name)
                if existing_item is not None
                else unknown_name
            )

        self.prompted_unknowns.add(unknown_name)

        print("\n" + "=" * 60)
        print("НАЙДЕН НЕИЗВЕСТНЫЙ ПРЕДМЕТ")
        print("=" * 60)
        print(f"Изображение сохранено:")
        print(full_path)
        print("")
        print(
            "Введите название предмета "
            "или нажмите Enter, чтобы оставить unknown-имя."
        )

        try:
            if callable(self.unknown_item_prompt):
                user_name = self.unknown_item_prompt(
                    unknown_name,
                    full_path
                ) or ""
            else:
                user_name = input(
                    f"Название для {unknown_name}: "
                ).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nВвод отменён. Будет использовано имя:")
            print(unknown_name)
            user_name = ""
        except Exception as error:
            print(f"[ПРЕДМЕТ] Ошибка окна названия: {error}")
            user_name = ""

        # Если пользователь ничего не ввёл,
        # сохраняем техническое имя.
        final_name = user_name if user_name else unknown_name

        if existing_item is None:
            existing_item = {
                "name": unknown_name,
                "images": [relative_path]
            }

            self.items_db.setdefault("items", []).append(existing_item)

        else:
            existing_item["name"] = unknown_name
            existing_item["images"] = [relative_path]
            existing_item.pop("image", None)

        # Если предмету дали нормальное название —
        # автоматически переименовываем и его фото:
        # unknown_items/unknown_xxx.png -> assets/items/<slug>.png
        if final_name and not final_name.startswith("unknown_"):
            rename_item_images(self.items_db, existing_item, final_name)

        save_json(ITEMS_FILE, self.items_db)

        print(f"\n[ПРЕДМЕТ СОХРАНЁН] {final_name}")
        print(f"[ФАЙЛ БАЗЫ] {ITEMS_FILE}")
        print("=" * 60 + "\n")

        return final_name

    # ========================================================
    # СКАНИРОВАНИЕ И JSON
    # ========================================================

    def save_chest_result(self, chest_data):
        """Добавляет один сундук в results/chests.json."""
        data = load_json(RESULTS_FILE, {"chests": []})

        if not isinstance(data, dict):
            data = {"chests": []}

        if "chests" not in data:
            data["chests"] = []

        data["chests"].append(chest_data)

        save_json(RESULTS_FILE, data)

    def scan_chest(self, force=False):
        # Сигнал подаётся именно в момент начала сканирования.
        self.play_scan_start_sound()
        time.sleep(0.25)

        screen = screenshot_bgr()

        if not self.chest_is_open(screen):
            if self.chest_was_open:
                print("[СУНДУК] Окно сундука закрыто.")
                self.chest_was_open = False
                self.last_chest_hash = None

            return None

        self.clear_recognition_debug()

        self.chest_was_open = True

        grid = self.get_grid_rect(screen)

        chest_image = screen[
            grid["y"]:grid["y"] + grid["height"],
            grid["x"]:grid["x"] + grid["width"]
        ]

        small_chest = resize_image(chest_image, 100, 60)

        chest_hash = hashlib.md5(
            small_chest.tobytes()
        ).hexdigest()

        if not force and chest_hash == self.last_chest_hash:
            return None

        self.last_chest_hash = chest_hash

        slots = self.get_slots(screen)

        slot_list = []
        totals = {}
        unknown_items = set()

        for slot in slots:
            slot_image = slot["image"]

            if slot_image is None or slot_image.size == 0:
                continue

            if self.is_empty_slot(slot_image):
                continue

            icon_image = self.extract_icon(slot_image)

            if icon_image is None or icon_image.size == 0:
                continue

            item_name, is_unknown, similarity = self.detect_item(
                icon_image
            )

            row = slot["row"] + 1
            column = slot["column"] + 1

            quantity = self.read_quantity(
                slot_image,
                debug_name=f"r{row}_c{column}_recognition"
            )

            if is_unknown:
                unknown_items.add(item_name)

            slot_list.append({
                "row": row,
                "column": column,
                "item": item_name,
                "quantity": quantity,
                "unknown": is_unknown,
                "similarity_score": round(float(similarity), 2)
            })

            totals[item_name] = totals.get(item_name, 0) + quantity

        chest_data = {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M:%S"),
            "screen_resolution": [
                screen.shape[1],
                screen.shape[0]
            ],
            "occupied_slots": len(slot_list),
            "unique_items": len(totals),
            "unknown_items": sorted(list(unknown_items)),
            "slots": slot_list,
            "totals": totals
        }

        self.save_chest_result(chest_data)

        self.play_success_sound()

        print("\n===================================================")
        print("[СУНДУК СЧИТАН]")
        print(f"Занято ячеек: {len(slot_list)}")
        print(f"Уникальных предметов: {len(totals)}")

        for item_name, quantity in sorted(totals.items()):
            print(f"  {item_name}: {quantity}")

        if unknown_items:
            print("Неизвестные предметы:")
            for item_name in sorted(unknown_items):
                print(f"  {item_name}")

        print("===================================================\n")

        return chest_data

    # ========================================================
    # БАЗА ПРЕДМЕТОВ (для интерфейса)
    # ========================================================

    def reload_items_db(self):
        """Перечитывает items.json с диска."""
        self.items_db = load_json(ITEMS_FILE, {"items": []})

        if not isinstance(self.items_db, dict):
            self.items_db = {"items": []}

        if "items" not in self.items_db:
            self.items_db["items"] = []

    def save_config(self):
        """Сохраняет текущую конфигурацию профиля в config.json."""
        save_json(CONFIG_FILE, self.config)

    def find_item_by_name(self, name):
        for item in self.items_db.get("items", []):
            if item.get("name") == name:
                return item
        return None

    def add_item_manual(self, name, source_image_path):
        """
        Добавляет предмет из файла изображения.
        Фото копируется в assets/items/<slug>.png.
        Возвращает (ok, message).
        """
        name = (name or "").strip()

        if not name:
            return False, "Название предмета не может быть пустым."

        if self.find_item_by_name(name):
            return False, f"Предмет «{name}» уже есть в базе."

        if not source_image_path or not os.path.isfile(source_image_path):
            return False, "Файл изображения не найден."

        image = cv2.imread(source_image_path, cv2.IMREAD_COLOR)

        if image is None:
            return False, "Не удалось открыть файл изображения."

        relative_path = store_new_item_image(
            self.items_db,
            image,
            name,
            source_path=None,  # оригинал пользователя не переносим, копируем
        )
        full_target = absolute_path(relative_path)
        if os.path.normpath(source_image_path) != os.path.normpath(full_target):
            import shutil
            shutil.copy2(source_image_path, full_target)

        self.items_db.setdefault("items", []).append({
            "name": name,
            "images": [relative_path],
        })

        save_json(ITEMS_FILE, self.items_db)
        return True, f"Предмет «{name}» добавлен. Файл: {relative_path}"

    def rename_item(self, old_name, new_name):
        """
        Переименовывает предмет и автоматически переименовывает
        все его фото. Возвращает (ok, message).
        """
        old_name = (old_name or "").strip()
        new_name = (new_name or "").strip()

        if not new_name:
            return False, "Новое название не может быть пустым."

        if new_name == old_name:
            return False, "Имя не изменилось."

        if self.find_item_by_name(new_name):
            return False, f"Предмет «{new_name}» уже есть в базе."

        item = self.find_item_by_name(old_name)

        if item is None:
            return False, "Предмет не найден в базе."

        new_paths = rename_item_images(self.items_db, item, new_name)
        save_json(ITEMS_FILE, self.items_db)

        return True, (
            f"«{old_name}» → «{new_name}». "
            f"Фото: {', '.join(os.path.basename(p) for p in new_paths)}"
        )

    def delete_item(self, name, delete_files=True):
        """Удаляет предмет из базы (и, опционально, его файлы)."""
        item = self.find_item_by_name(name)

        if item is None:
            return False, "Предмет не найден в базе."

        removed_files = []

        if delete_files:
            for path in item_images_of(item):
                full = absolute_path(path)
                try:
                    if os.path.isfile(full):
                        os.remove(full)
                        removed_files.append(os.path.basename(path))
                except OSError:
                    pass

        self.items_db["items"] = [
            entry for entry in self.items_db.get("items", [])
            if entry.get("name") != name
        ]

        save_json(ITEMS_FILE, self.items_db)
        return True, f"Предмет «{name}» удалён. Файлов удалено: {len(removed_files)}"

    def add_item_image(self, name, source_image_path):
        """
        Добавляет ещё одно фото к существующему предмету
        (несколько шаблонов для одного предмета).
        """
        item = self.find_item_by_name(name)

        if item is None:
            return False, "Предмет не найден в базе."

        if not source_image_path or not os.path.isfile(source_image_path):
            return False, "Файл изображения не найден."

        image = cv2.imread(source_image_path, cv2.IMREAD_COLOR)

        if image is None:
            return False, "Не удалось открыть файл изображения."

        images = item_images_of(item)
        item["images"] = images  # нормализуем формат

        relative_path = next_image_slot_path(self.items_db, item)
        full_target = absolute_path(relative_path)
        os.makedirs(os.path.dirname(full_target), exist_ok=True)

        import shutil
        shutil.copy2(source_image_path, full_target)

        item["images"].append(relative_path)
        item.pop("image", None)

        save_json(ITEMS_FILE, self.items_db)
        return True, f"Добавлено фото {os.path.basename(relative_path)} " \
                     f"(шаблонов: {len(item['images'])})"

    def remove_item_image(self, name, image_path):
        """Убирает одно фото из списка шаблонов предмета."""
        item = self.find_item_by_name(name)

        if item is None:
            return False, "Предмет не найден в базе."

        images = item_images_of(item)

        if image_path not in images:
            return False, "Фото не найдено в списке предмета."

        remaining = [path for path in images if path != image_path]

        if not remaining:
            return False, "Нельзя удалить последнее фото предмета."

        full = absolute_path(image_path)
        try:
            if os.path.isfile(full):
                os.remove(full)
        except OSError:
            pass

        item["images"] = remaining
        item.pop("image", None)
        save_json(ITEMS_FILE, self.items_db)

        return True, f"Фото {os.path.basename(image_path)} удалено."

    def merge_items(self, from_name, into_name):
        """Объединяет два предмета: фото and записи сливаются в один."""
        source = self.find_item_from_alias(from_name)
        target = self.find_item_from_alias(into_name)

        if source is None:
            return False, f"Предмет «{from_name}» не найден."
        if target is None:
            return False, f"Предмет «{into_name}» не найден."
        if source is target:
            return False, "Это один и тот же предмет."

        merged_images = item_images_of(target)

        for path in item_images_of(source):
            if path not in merged_images:
                merged_images.append(path)

        target["images"] = merged_images
        target.pop("image", None)

        self.items_db["items"] = [
            entry for entry in self.items_db.get("items", [])
            if entry is not source
        ]

        save_json(ITEMS_FILE, self.items_db)
        return True, (
            f"«{source.get('name')}» объединён с «{target.get('name')}». "
            f"Шаблонов: {len(merged_images)}"
        )

    def find_item_from_alias(self, name):
        """
        Ищет предмет по имени или по unknown-алиасу
        (unknown_<hash>, имя файла без расширения).
        """
        item = self.find_item_by_name(name)

        if item is not None:
            return item

        stem = os.path.splitext(name)[0]

        for entry in self.items_db.get("items", []):
            for path in item_images_of(entry):
                file_stem = os.path.splitext(os.path.basename(path))[0]
                if file_stem == stem:
                    return entry

        return None

    def resolve_unknown_item(self, unknown_name, real_name, temp_image_path):
        """
        Превращает unknown_XXXX в полноценный предмет:
        сохраняет имя и переименовывает фото в assets/items/.
        Используется мастером первоначальной настройки.
        """
        if not real_name or not real_name.strip():
            return False, "Пустое название."

        real_name = real_name.strip()

        item = None
        for entry in self.items_db.get("items", []):
            if entry.get("name") == unknown_name:
                item = entry
                break

        if item is None and temp_image_path and os.path.isfile(temp_image_path):
            item = {
                "name": unknown_name,
                "images": [os.path.relpath(temp_image_path, BASE_DIR)
                           .replace("\\", "/")],
            }
            self.items_db.setdefault("items", []).append(item)

        if item is None:
            return False, "Unknown-предмет не найден."

        existing_real = self.find_item_by_name(real_name)

        if existing_real is not None and existing_real is not item:
            # Название уже занято — просто объединяем шаблоны.
            ok, message = self.merge_items(item.get("name"), real_name)
            return ok, message

        new_paths = rename_item_images(self.items_db, item, real_name)
        save_json(ITEMS_FILE, self.items_db)

        return True, f"Сохранён: {real_name} ({os.path.basename(new_paths[0])})"

    # ========================================================
    # DISCORD
    # ========================================================

    @staticmethod
    def format_number(number):
        """12345 -> 12 345"""
        return f"{number:,}".replace(",", " ")

    def build_discord_message(self, chests):
        """Формирует текстовый отчёт Discord без эмодзи в таблице."""
        if not chests:
            return (
                "**База сундуков пуста**\n"
                "Сначала откройте сундук и нажмите `F8`."
            )

        discord_config = self.config.get("discord_message", {})
        global_totals = {}
        all_unknown_items = set()

        for chest in chests:
            for item_name, quantity in chest.get("totals", {}).items():
                global_totals[item_name] = global_totals.get(item_name, 0) + quantity
            for unknown_item in chest.get("unknown_items", []):
                all_unknown_items.add(unknown_item)

        last_timestamp = chests[-1].get("timestamp", "неизвестно")
        stack_size = discord_config.get("stack_size", 64)
        table_config = discord_config.get("table", {})
        name_width = int(table_config.get("name_width", 30))
        total_width = int(table_config.get("total_width", 12))
        stacks_width = int(table_config.get("stacks_width", 10))
        border = table_config.get("border", "│")
        header_border = table_config.get("header_border", "─")

        def fit(text, width, align="left"):
            text = str(text)
            if len(text) > width:
                text = text[:max(1, width - 1)] + "…"
            return text.rjust(width) if align == "right" else text.ljust(width)

        def table_line(name, total, stacks):
            return (
                f"{fit(name, name_width)} {border} "
                f"{fit(total, total_width, 'right')} {border} "
                f"{fit(stacks, stacks_width, 'right')}"
            )

        columns = table_line("Название", "Всего", "Стаков")
        separator = (
            f"{header_border * name_width}─┼─"
            f"{header_border * total_width}─┼─"
            f"{header_border * stacks_width}"
        )

        item_lines = []
        for item_name, quantity in sorted(global_totals.items(), key=lambda item: item[0].lower()):
            if stack_size and quantity < stack_size:
                stacks_text = ""
            else:
                stacks = quantity / stack_size if stack_size else quantity
                stacks_text = str(int(stacks)) if stacks == int(stacks) else f"{stacks:.2f}".rstrip("0").rstrip(".")
            item_lines.append(table_line(item_name, self.format_number(quantity), stacks_text))

        items_text = "```text\n" + "\n".join([columns, separator] + item_lines) + "\n```"
        values = {"chests": len(chests), "last_scan": last_timestamp, "items": items_text, "unknowns": "\n".join(sorted(all_unknown_items))}

        header = discord_config.get("header", "")
        scanned_chests = discord_config.get("scanned_chests", "Отсканировано сундуков: {chests}").format(**values)
        last_scan = discord_config.get("last_scan", "Время последнего сканирования: {last_scan}").format(**values)
        items_title = discord_config.get("items_title", "══════════Предметы══════════")

        lines = []
        if header:
            lines.extend([header, ""])
        lines.extend([scanned_chests, last_scan, "", items_title, "", items_text])

        if all_unknown_items:
            unknown_title = discord_config.get("unknown_title", "══════════Неизвестные предметы══════════")
            unknown_template = discord_config.get("unknown_item", "• {name}")
            unknowns_text = discord_config.get("item_separator", "\n").join(unknown_template.format(name=name) for name in sorted(all_unknown_items))
            lines.extend(["", unknown_title, "", unknowns_text])

        return "\n".join(lines)


    def send_test_to_webhook(self, webhook_url):
        """Отправляет короткое тестовое сообщение в указанный вебхук."""
        try:
            response = requests.post(
                webhook_url,
                json={
                    "content": (
                        "✅ **Chest Scanner** — тестовое сообщение. "
                        f"Профиль: `{self.active_profile}`."
                    )
                },
                timeout=10,
            )

            if response.status_code in (204, 200):
                return True, "Доставлено ✓ Discord принял вебхук."

            if response.status_code == 401:
                return False, "Вебхук отозван или URL неверный (401)."
            if response.status_code == 404:
                return False, "Вебхук не найден (404). Создайте заново."

            return False, f"Discord вернул код {response.status_code}."
        except requests.exceptions.RequestException as error:
            return False, f"Сетевая ошибка: {error}"

    def get_discord_webhook_url(self):
        """
        Получает Discord Webhook.
        Приоритет:
        1. discord_webhook.json
        2. переменная окружения из config.json
        """
        env_name = self.config.get(
            "discord_webhook_env",
            "DISCORD_WEBHOOK_URL"
        )

        webhook_file = DISCORD_WEBHOOK_FILE

        # Основной способ: отдельный JSON-файл внутри профиля,
        # который создаётся из интерфейса (мастер настройки).
        if os.path.exists(webhook_file):
            try:
                data = load_json(webhook_file, {})

                if isinstance(data, dict):
                    webhook_url = (
                        data.get("webhook_url")
                        or data.get(env_name)
                        or data.get("DISCORD_WEBHOOK_URL")
                    )

                    if isinstance(webhook_url, str):
                        webhook_url = webhook_url.strip()

                        if webhook_url:
                            return webhook_url

            except Exception as error:
                print(
                    f"[DISCORD] Ошибка чтения webhook-файла: {error}"
                )

        # Запасной вариант: переменная окружения.
        webhook_url = os.getenv(env_name, "").strip()

        if webhook_url:
            return webhook_url

        return None

    @staticmethod
    def load_discord_message_ids():
        """Возвращает ID сообщений последнего отчёта."""
        data = load_json(DISCORD_MESSAGE_IDS_FILE, {"message_ids": []})
        ids = data.get("message_ids", []) if isinstance(data, dict) else []
        return [str(message_id) for message_id in ids if message_id]

    @staticmethod
    def save_discord_message_ids(message_ids):
        """Сохраняет ID сообщений текущего отчёта."""
        save_json(
            DISCORD_MESSAGE_IDS_FILE,
            {"message_ids": [str(message_id) for message_id in message_ids]}
        )

    @staticmethod
    def _discord_response_error(response):
        try:
            body = response.json()
            if isinstance(body, dict):
                message = body.get("message")
                if message:
                    return str(message)
        except ValueError:
            pass
        return response.text[:500]

    def _create_discord_message(self, webhook_url, content):
        """Создаёт обычное текстовое сообщение Discord."""
        try:
            payload = {
                "content": content,
                "username": "Chest Scanner"
            }
            response = requests.post(
                webhook_url,
                params={"wait": "true"},
                json=payload,
                timeout=30
            )

            if response.status_code != 200:
                return None, (
                    f"Discord HTTP {response.status_code}: "
                    f"{self._discord_response_error(response)}"
                )

            try:
                response_data = response.json()
                message_id = response_data.get("id")
            except ValueError:
                message_id = None

            if not message_id:
                return None, "Discord не вернул ID созданного сообщения."

            return str(message_id), None
        except requests.RequestException as error:
            return None, f"Ошибка сети: {error}"

    def _edit_discord_message(self, webhook_url, message_id, content):
        """Редактирует существующее текстовое сообщение Discord."""
        try:
            payload = {
                "content": content,
                "username": "Chest Scanner"
            }
            response = requests.patch(
                f"{webhook_url}/messages/{message_id}",
                json=payload,
                timeout=30
            )

            if response.status_code == 404:
                return False, "not_found"

            if response.status_code != 200:
                return False, (
                    f"Discord HTTP {response.status_code}: "
                    f"{self._discord_response_error(response)}"
                )

            return True, None
        except requests.RequestException as error:
            return False, f"Ошибка сети: {error}"

    def _delete_discord_message(self, webhook_url, message_id):
        """Удаляет только лишнее старое сообщение отчёта."""
        try:
            response = requests.delete(
                f"{webhook_url}/messages/{message_id}",
                timeout=15
            )
            if response.status_code in (200, 204, 404):
                return True, None
            return False, (
                f"Discord HTTP {response.status_code}: "
                f"{self._discord_response_error(response)}"
            )
        except requests.RequestException as error:
            return False, f"Ошибка сети: {error}"

    def archive_report_after_discord(self):
        """Переносит успешно отправленный отчёт в историю и очищает текущий файл."""
        data = load_json(RESULTS_FILE, {"chests": []})
        chests = data.get("chests", []) if isinstance(data, dict) else []
        if not chests:
            return None

        timestamp = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%Y-%m-%d_%H-%M-%S")
        history_path = os.path.join(HISTORY_DIR, f"report_{timestamp}.json")
        counter = 2
        while os.path.exists(history_path):
            history_path = os.path.join(
                HISTORY_DIR, f"report_{timestamp}_{counter}.json"
            )
            counter += 1

        save_json(history_path, data)
        save_json(RESULTS_FILE, {"chests": []})
        return history_path

    def send_all_chests_to_discord(self):
        """Синхронизирует текстовый отчёт в Discord и архивирует его после успеха."""
        webhook_url = self.get_discord_webhook_url()
        if not webhook_url:
            return False, "Discord Webhook не найден."
        if not webhook_url.startswith((
            "https://discord.com/api/webhooks/",
            "https://discordapp.com/api/webhooks/"
        )):
            return False, "Некорректный URL Discord Webhook."
        if not os.path.exists(RESULTS_FILE):
            return False, "Файл chests.json не найден."

        data = load_json(RESULTS_FILE, {"chests": []})
        chests = data.get("chests", []) if isinstance(data, dict) else []
        if not chests:
            return False, "Нет сохранённых сканирований."

        full_message = self.build_discord_message(chests)
        message_parts = split_discord_message(full_message, limit=1900) or ["Отчёт пуст."]
        total_parts = len(message_parts)

        contents = []
        for index, part in enumerate(message_parts):
            if index == 0:
                content = part
            else:
                prefix = f"**Продолжение отчёта ({index + 1}/{total_parts})**\n\n"
                content = (prefix + part)[:1900]
            contents.append(content)

        old_ids = self.load_discord_message_ids()
        new_ids = []
        edited = 0
        created = 0
        deleted = 0

        try:
            for index, content in enumerate(contents):
                if index < len(old_ids):
                    message_id = old_ids[index]
                    ok, error = self._edit_discord_message(
                        webhook_url,
                        message_id,
                        content
                    )

                    if ok:
                        new_ids.append(message_id)
                        edited += 1
                    elif error == "not_found":
                        created_id, create_error = self._create_discord_message(
                            webhook_url,
                            content
                        )
                        if not created_id:
                            return False, f"Не удалось восстановить сообщение: {create_error}"
                        new_ids.append(created_id)
                        created += 1
                    else:
                        return False, f"Не удалось отредактировать сообщение {message_id}: {error}"
                else:
                    message_id, error = self._create_discord_message(
                        webhook_url,
                        content
                    )
                    if not message_id:
                        return False, f"Не удалось создать сообщение: {error}"
                    new_ids.append(message_id)
                    created += 1

                time.sleep(0.5)

            # Если новый отчёт короче старого — удаляем только хвост.
            for message_id in old_ids[total_parts:]:
                ok, error = self._delete_discord_message(webhook_url, message_id)
                if ok:
                    deleted += 1
                else:
                    return False, f"Не удалось удалить лишнее сообщение {message_id}: {error}"
                time.sleep(0.3)

            self.save_discord_message_ids(new_ids)

            history_path = self.archive_report_after_discord()
            if history_path:
                history_name = os.path.basename(history_path)
                return True, (
                    f"Discord обновлён: изменено {edited}, создано {created}, "
                    f"удалено лишних {deleted}. Отчёт сохранён в истории: {history_name}. "
                    "Текущий chests.json очищен."
                )

            return True, (
                f"Discord обновлён: изменено {edited}, создано {created}, "
                f"удалено лишних {deleted}."
            )

        except requests.RequestException as error:
            error_text = f"Ошибка сети: {error}"
            print(f"[DISCORD] {error_text}")
            return False, error_text
        except Exception as error:
            error_text = f"Неожиданная ошибка: {error}"
            print(f"[DISCORD] {error_text}")
            return False, error_text


# ============================================================
# ГРАФИЧЕСКИЙ ИНТЕРФЕЙС
# ============================================================

class Theme:
    """Общая тёмная палитра для всех окон программы."""

    BG = "#101318"
    PANEL = "#171b22"
    PANEL_2 = "#1d232c"
    BORDER = "#2a313c"
    TEXT = "#f2f4f7"
    MUTED = "#8f9aaa"
    ACCENT = "#5865f2"
    ACCENT_HOVER = "#6975ff"
    GREEN = "#35c98b"
    RED = "#f25f67"
    YELLOW = "#f0b84b"


def themed_button(parent, text, command, accent=False, theme=Theme):
    bg = theme.ACCENT if accent else theme.PANEL_2
    active = theme.ACCENT_HOVER if accent else "#252c36"
    button = tk.Button(
        parent,
        text=text,
        command=command,
        bg=bg,
        fg=theme.TEXT,
        activebackground=active,
        activeforeground=theme.TEXT,
        relief="flat",
        bd=0,
        padx=16,
        pady=8,
        cursor="hand2",
        font=("Segoe UI", 10, "bold"),
    )
    button.bind("<Enter>", lambda e: button.configure(bg=active))
    button.bind("<Leave>", lambda e: button.configure(bg=bg))
    return button


def style_dialog(dialog, theme=Theme):
    dialog.configure(bg=theme.BG)


def load_photo_image(path, zoom_if_small=True):
    """Загружает PNG/JPG как tk.PhotoImage (PNG) или через cv2->PNG."""
    if not path or not os.path.isfile(path):
        return None

    try:
        image = tk.PhotoImage(file=path)
        if zoom_if_small and image.width() <= 48 and image.height() <= 48:
            image = image.zoom(2, 2)
        return image
    except tk.TclError:
        pass

    # Не-PNG форматы tk.PhotoImage не умеет — конвертируем во временный PNG.
    try:
        import tempfile
        image = cv2.imread(path, cv2.IMREAD_COLOR)

        if image is None:
            return None

        temp_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}.png")
        cv2.imwrite(temp_path, image)
        photo = tk.PhotoImage(file=temp_path)
        try:
            os.remove(temp_path)
        except OSError:
            pass

        if zoom_if_small and photo.width() <= 48 and photo.height() <= 48:
            photo = photo.zoom(2, 2)
        return photo
    except Exception:
        return None


# ============================================================
# МАСТЕР ПЕРВОНАЧАЛЬНОЙ НАСТРОЙКИ
# ============================================================

class SetupWizard(tk.Toplevel):
    """
    Пошаговый мастер: профиль -> Discord -> база unknown-предметов.
    Открывается автоматически при первом запуске профиля.
    """

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.reader = app.reader

        self.title("Мастер первоначальной настройки")
        self.geometry("720x560")
        self.resizable(False, False)
        self.configure(bg=Theme.BG)
        self.transient(app.root)
        self.grab_set()

        self.step = 0
        self.unknown_queue = []
        self.unknown_index = 0
        self._photo_refs = []

        self.header = tk.Label(
            self, text="", bg=Theme.BG, fg=Theme.TEXT,
            font=("Segoe UI", 17, "bold")
        )
        self.header.pack(anchor="w", padx=28, pady=(24, 2))

        self.subtitle = tk.Label(
            self, text="", bg=Theme.BG, fg=Theme.MUTED,
            font=("Segoe UI", 10)
        )
        self.subtitle.pack(anchor="w", padx=28)

        self.body = tk.Frame(self, bg=Theme.BG)
        self.body.pack(fill="both", expand=True, padx=28, pady=(16, 0))

        self.footer = tk.Frame(self, bg=Theme.BG)
        self.footer.pack(fill="x", padx=28, pady=18)

        self.back_button = themed_button(self.footer, "Назад", self.go_back)
        self.back_button.pack(side="left")

        self.progress_label = tk.Label(
            self.footer, text="", bg=Theme.BG, fg=Theme.MUTED,
            font=("Segoe UI", 9)
        )
        self.progress_label.pack(side="left", padx=14)

        self.next_button = themed_button(
            self.footer, "Далее", self.go_next, accent=True
        )
        self.next_button.pack(side="right")

        self.skip_button = themed_button(self.footer, "Пропустить", self.go_next)
        self.skip_button.pack(side="right", padx=(0, 8))

        self.show_step()

    # ------------------------------------------------------------

    def clear_body(self):
        for child in self.body.winfo_children():
            child.destroy()
        self._photo_refs.clear()

    def go_back(self):
        if self.step > 0:
            self.step -= 1
            self.show_step()

    def go_next(self):
        handler = (self.save_current_step,)
        ok, message = handler[0]()

        if not ok:
            if message:
                messagebox.showwarning("Внимание", message, parent=self)
            return

        self.step += 1

        if self.step == 2:
            self.build_unknown_list()

        if self.step >= 4:
            self.finish()
            return

        self.show_step()

    def save_current_step(self):
        if self.step == 0:
            return self.save_profile_step()
        if self.step == 1:
            return self.save_discord_step()
        return True, None

    # ------------------- ШАГ 1: профиль -------------------------

    def show_step(self):
        self.clear_body()

        steps = [
            ("Профиль", "Выберите или создайте профиль. У каждого профиля "
                        "своя база предметов, настройки и Discord.", False),
            ("Discord", "Настройте вебхук для отправки отчётов. "
                        "Можно пропустить и настроить позже.", False),
            ("База предметов", "", True),
            ("Готово", "Мастер завершён. Все изменения сохранены.", False),
        ]

        title, description, progress_only = steps[self.step]
        self.header.configure(text=f"Шаг {self.step + 1} из 4 — {title}")
        self.subtitle.configure(text=description)

        self.progress_label.configure(
            text="" if self.step != 2
            else f"{min(self.unknown_index + 1, max(1, len(self.unknown_queue)))} "
                 f"из {len(self.unknown_queue)}"
        )

        last_step = self.step >= 3
        self.next_button.configure(
            text="Готово" if last_step else "Далее",
            command=self.finish if last_step else self.go_next,
        )
        self.back_button.configure(state=("disabled" if self.step == 0 else "normal"))
        self.skip_button.configure(
            state=("normal" if self.step in (1, 2) else "hidden")
        )

        if self.step == 0:
            self.show_profile_step()
        elif self.step == 1:
            self.show_discord_step()
        elif self.step == 2:
            self.show_unknown_step()
        else:
            self.show_done_step()

    def show_profile_step(self):
        frame = tk.Frame(self.body, bg=Theme.PANEL,
                         highlightbackground=Theme.BORDER, highlightthickness=1)
        frame.pack(fill="x")

        tk.Label(frame, text="Активный профиль:", bg=Theme.PANEL, fg=Theme.MUTED,
                 font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w",
                 padx=16, pady=(14, 4))

        self.profile_combo = ttk.Combobox(
            frame, values=get_profiles(), state="readonly", width=30,
            font=("Segoe UI", 11)
        )
        self.profile_combo.set(self.reader.active_profile)
        self.profile_combo.grid(row=1, column=0, sticky="w", padx=16, pady=(0, 14))

        tk.Label(frame, text="Новый профиль:", bg=Theme.PANEL, fg=Theme.MUTED,
                 font=("Segoe UI", 9, "bold")).grid(row=0, column=1, sticky="w",
                 padx=16, pady=(14, 4))

        self.new_profile_entry = tk.Entry(
            frame, bg=Theme.PANEL_2, fg=Theme.TEXT, insertbackground=Theme.TEXT,
            relief="flat", font=("Segoe UI", 11), width=24
        )
        self.new_profile_entry.grid(row=1, column=1, sticky="w", padx=16, pady=(0, 6))

        themed_button(frame, "Создать", self.create_profile_from_wizard) \
            .grid(row=1, column=2, sticky="w", padx=(0, 16), pady=(0, 14))

        note = tk.Label(
            self.body, bg=Theme.BG, fg=Theme.MUTED, justify="left",
            font=("Segoe UI", 9),
            text="Смена профиля перезапустит программу.\n"
                 "Данные каждого профиля хранятся в папке profiles/<имя>."
        )
        note.pack(anchor="w", pady=(12, 0))

    def create_profile_from_wizard(self):
        name = sanitize_profile_name(self.new_profile_entry.get())

        if not name:
            messagebox.showwarning("Внимание", "Введите имя профиля.", parent=self)
            return

        profile_dir = os.path.join(PROFILES_DIR, name)

        if os.path.exists(profile_dir):
            messagebox.showwarning(
                "Внимание", f"Профиль «{name}» уже существует.", parent=self
            )
            return

        set_active_profile(name)
        self.app.restart_with_profile(name)

    def save_profile_step(self):
        selected = self.profile_combo.get().strip()

        if selected and selected != self.reader.active_profile:
            set_active_profile(selected)
            self.app.restart_with_profile(selected)
            return False, None  # программа будет перезапущена

        return True, None

    # ------------------- ШАГ 2: discord -------------------------

    def show_discord_step(self):
        frame = tk.Frame(self.body, bg=Theme.PANEL,
                         highlightbackground=Theme.BORDER, highlightthickness=1)
        frame.pack(fill="x")

        tk.Label(frame, text="URL вебхука Discord:", bg=Theme.PANEL,
                 fg=Theme.MUTED, font=("Segoe UI", 9, "bold")).grid(
            row=0, column=0, sticky="w", padx=16, pady=(14, 4))

        current = ""
        if os.path.exists(DISCORD_WEBHOOK_FILE):
            current = load_json(DISCORD_WEBHOOK_FILE, {}).get("webhook_url", "")

        self.webhook_entry = tk.Entry(
            frame, bg=Theme.PANEL_2, fg=Theme.TEXT, insertbackground=Theme.TEXT,
            relief="flat", font=("Segoe UI", 10), width=64
        )
        self.webhook_entry.grid(row=1, column=0, columnspan=3, sticky="we",
                                padx=16, pady=(0, 10))
        self.webhook_entry.insert(0, current or "")

        self.test_result = tk.Label(frame, text="", bg=Theme.PANEL,
                                    fg=Theme.MUTED, font=("Segoe UI", 9))
        self.test_result.grid(row=2, column=0, sticky="w", padx=16, pady=(0, 14))

        themed_button(frame, "Сохранить", self.save_wizard_webhook) \
            .grid(row=1, column=3, padx=(0, 16))
        themed_button(frame, "Проверить", self.test_wizard_webhook) \
            .grid(row=2, column=3, pady=(0, 14))

        hint = tk.Label(
            self.body, bg=Theme.BG, fg=Theme.MUTED, justify="left",
            font=("Segoe UI", 9),
            text="Как получить вебхук: настройки канала Discord → "
                 "Интеграции → Вебхуки → Новый вебхук → скопировать URL.\n"
                 "Вебхук сохраняется отдельно для каждого профиля."
        )
        hint.pack(anchor="w", pady=(12, 0))

    def wizard_webhook_value(self):
        url = self.webhook_entry.get().strip()

        if url and not url.startswith((
            "https://discord.com/api/webhooks/",
            "https://discordapp.com/api/webhooks/",
        )):
            return None, "URL должен начинаться с https://discord.com/api/webhooks/"

        return url, None

    def save_wizard_webhook(self):
        url, error = self.wizard_webhook_value()

        if error:
            self.test_result.configure(text=error, fg=Theme.RED)
            return

        data = {"webhook_url": url or ""}
        save_json(DISCORD_WEBHOOK_FILE, data)
        self.test_result.configure(
            text="Сохранено." if url else "Пусто — вебхук не задан.",
            fg=Theme.GREEN if url else Theme.MUTED,
        )
        self.app._log("Вебхук Discord сохранён (мастер настройки).", Theme.GREEN)

    def test_wizard_webhook(self):
        url, error = self.wizard_webhook_value()

        if error or not url:
            self.test_result.configure(
                text=error or "Введите URL вебхука.", fg=Theme.RED
            )
            return

        self.test_result.configure(text="Отправка тестового сообщения…",
                                   fg=Theme.MUTED)

        def worker():
            ok, message = self.reader.send_test_to_webhook(url)
            color = Theme.GREEN if ok else Theme.RED
            self.after(0, lambda: self.test_result.configure(
                text=message, fg=color
            ))

        threading.Thread(target=worker, daemon=True).start()

    def save_discord_step(self):
        url, error = self.wizard_webhook_value()

        if error:
            return False, error

        if url:
            save_json(DISCORD_WEBHOOK_FILE, {"webhook_url": url})

        return True, None

    # ------------- ШАГ 3: разбор unknown-предметов --------------

    def build_unknown_list(self):
        self.reader.reload_items_db()
        items = self.reader.items_db.get("items", [])

        known_names = {
            item.get("name") for item in items
            if item.get("name") and not item.get("name", "").startswith("unknown_")
        }

        queue = []

        for item in items:
            name = item.get("name", "")

            if not name.startswith("unknown_"):
                continue

            images = item_images_of(item)
            image_path = None

            for path in images:
                full = absolute_path(path)
                if os.path.isfile(full):
                    image_path = full
                    break

            queue.append({
                "name": name,
                "image": image_path,
                "suggestion": "",
            })

        queue.sort(key=lambda entry: entry["name"])

        # Дополняем список файлами, которых нет в базе.
        if os.path.isdir(UNKNOWN_DIR):
            listed = {entry["name"] for entry in queue}
            for filename in sorted(os.listdir(UNKNOWN_DIR)):
                if not filename.lower().endswith(".png"):
                    continue
                stem = os.path.splitext(filename)[0]
                if stem in listed:
                    continue
                queue.append({
                    "name": stem,
                    "image": os.path.join(UNKNOWN_DIR, filename),
                    "suggestion": "",
                })

        self.unknown_queue = queue
        self.unknown_index = 0

    def show_unknown_step(self):
        if not self.unknown_queue:
            frame = tk.Frame(self.body, bg=Theme.BG)
            frame.pack(fill="both", expand=True)
            tk.Label(frame, text="База предметов чиста 🎉", bg=Theme.BG,
                     fg=Theme.GREEN, font=("Segoe UI", 15, "bold")).pack(pady=(40, 8))
            tk.Label(frame,
                     text="Неизвестных предметов не найдено.\n"
                          "Можно завершать мастер.",
                     bg=Theme.BG, fg=Theme.MUTED,
                     font=("Segoe UI", 10)).pack()
            self.progress_label.configure(text="0 из 0")
            return

        if self.unknown_index >= len(self.unknown_queue):
            self.show_unknown_summary()
            return

        entry = self.unknown_queue[self.unknown_index]

        left = tk.Frame(self.body, bg=Theme.PANEL,
                        highlightbackground=Theme.BORDER, highlightthickness=1,
                        width=180, height=180)
        left.pack(side="left", anchor="n", padx=(0, 22))
        left.pack_propagate(False)

        icon_label = tk.Label(left, text="Нет\nиконки", bg=Theme.PANEL,
                              fg=Theme.MUTED, font=("Segoe UI", 9))
        icon_label.pack(expand=True)

        if entry["image"]:
            photo = load_photo_image(entry["image"])
            if photo is not None:
                self._photo_refs.append(photo)
                icon_label.configure(image=photo, text="")
                icon_label.image = photo

        right = tk.Frame(self.body, bg=Theme.BG)
        right.pack(side="left", fill="both", expand=True)

        tk.Label(right, text=f"Шаблон: {entry['name']}", bg=Theme.BG,
                 fg=Theme.MUTED, font=("Segoe UI", 9)).pack(anchor="w")

        tk.Label(right, text="Как называется этот предмет?", bg=Theme.BG,
                 fg=Theme.TEXT, font=("Segoe UI", 12, "bold")).pack(
            anchor="w", pady=(16, 6))

        suggestion_frame = tk.Frame(right, bg=Theme.BG)
        suggestion_frame.pack(fill="x")

        self.unknown_suggest = ttk.Combobox(
            suggestion_frame, values=[], state="normal", width=34,
            font=("Segoe UI", 11)
        )
        self.unknown_suggest.set(entry.get("suggestion", ""))
        self.unknown_suggest.pack(side="left")

        themed_button(suggestion_frame, "Сохранить", self.resolve_current_unknown,
                      accent=True).pack(side="left", padx=(10, 0))

        skip = themed_button(right, "Пропустить этот предмет", self.next_unknown)
        skip.pack(anchor="w", pady=(22, 0))

        names = sorted(
            item.get("name") for item in self.reader.items_db.get("items", [])
            if item.get("name") and not item.get("name").startswith("unknown_")
        )
        self.unknown_suggest.configure(values=names)

        self.progress_label.configure(
            text=f"{self.unknown_index + 1} из {len(self.unknown_queue)}"
        )

    def resolve_current_unknown(self):
        entry = self.unknown_queue[self.unknown_index]
        real_name = self.unknown_suggest.get().strip()
        entry["suggestion"] = real_name

        if not real_name:
            messagebox.showwarning(
                "Внимание", "Введите название или пропустите предмет.",
                parent=self
            )
            return

        ok, message = self.reader.resolve_unknown_item(
            entry["name"], real_name, entry["image"]
        )

        if ok:
            self.app._log(f"Мастер: {message}", Theme.GREEN)
        else:
            messagebox.showwarning("Ошибка", message, parent=self)
            return

        self.next_unknown()

    def next_unknown(self):
        self.unknown_index += 1
        self.show_step()

    def show_unknown_summary(self):
        frame = tk.Frame(self.body, bg=Theme.BG)
        frame.pack(fill="both", expand=True)

        remaining = len(self.unknown_queue) - self.unknown_index
        tk.Label(frame, text="Разбор неизвестных предметов завершён",
                 bg=Theme.BG, fg=Theme.GREEN,
                 font=("Segoe UI", 14, "bold")).pack(pady=(30, 6))
        tk.Label(frame,
                 text="Все предметы обработаны. Нажмите «Далее», чтобы завершить мастер.",
                 bg=Theme.BG, fg=Theme.MUTED, font=("Segoe UI", 10)).pack()
        self.progress_label.configure(text=f"{len(self.unknown_queue)} из "
                                           f"{len(self.unknown_queue)}")

    # ------------------- ШАГ 4: готово --------------------------

    def show_done_step(self):
        frame = tk.Frame(self.body, bg=Theme.BG)
        frame.pack(fill="both", expand=True)

        tk.Label(frame, text="Настройка завершена ✅", bg=Theme.BG,
                 fg=Theme.GREEN, font=("Segoe UI", 18, "bold")).pack(pady=(50, 10))
        tk.Label(frame,
                 text="Теперь можно сканировать сундуки (F8)\n"
                      "и отправлять отчёты в Discord (F9).\n\n"
                      "Мастер можно открыть снова через кнопку «Мастер»,\n"
                      "а Discord — изменить через кнопку «Discord».",
                 bg=Theme.BG, fg=Theme.MUTED, font=("Segoe UI", 10),
                 justify="center").pack()

    def finish(self):
        with open(SETUP_DONE_FLAG, "w", encoding="utf-8") as flag:
            flag.write(datetime.now(ZoneInfo("Europe/Moscow")).isoformat())

        self.app.reader.reload_items_db()
        self.app._refresh_stats()
        self.app._log("Мастер первоначальной настройки завершён.", Theme.GREEN)
        self.destroy()


# ============================================================
# ДИАЛОГ УПРАВЛЕНИЯ БАЗОЙ ПРЕДМЕТОВ
# ============================================================

class ItemsDatabaseDialog(tk.Toplevel):
    """
    Полноценное окно работы с базой предметов:
    поиск, добавление, переименование (с автопереименованием фото),
    несколько фото у одного предмета, удаление, объединение.
    """

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.reader = app.reader

        self.title("База предметов")
        self.geometry("940x640")
        self.minsize(800, 520)
        self.configure(bg=Theme.BG)
        self.transient(app.root)

        self._photo_cache = {}

        toolbar = tk.Frame(self, bg=Theme.BG)
        toolbar.pack(fill="x", padx=18, pady=(16, 8))

        tk.Label(toolbar, text="Поиск:", bg=Theme.BG, fg=Theme.MUTED,
                 font=("Segoe UI", 10)).pack(side="left")

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh_list())

        search_entry = tk.Entry(
            toolbar, textvariable=self.search_var,
            bg=Theme.PANEL_2, fg=Theme.TEXT, insertbackground=Theme.TEXT,
            relief="flat", font=("Segoe UI", 10), width=30
        )
        search_entry.pack(side="left", padx=8, ipady=6)

        themed_button(toolbar, "Добавить предмет", self.add_item, accent=True) \
            .pack(side="right")
        themed_button(toolbar, "Обновить", self.refresh_list).pack(
            side="right", padx=(0, 8))

        middle = tk.Frame(self, bg=Theme.BG)
        middle.pack(fill="both", expand=True, padx=18, pady=(0, 8))

        list_panel = tk.Frame(middle, bg=Theme.PANEL,
                              highlightbackground=Theme.BORDER,
                              highlightthickness=1)
        list_panel.pack(side="left", fill="both", expand=True)

        columns_frame = tk.Frame(list_panel, bg=Theme.PANEL)
        columns_frame.pack(fill="x", padx=10, pady=(10, 0))

        tree_frame = tk.Frame(list_panel, bg=Theme.PANEL)
        tree_frame.pack(fill="both", expand=True, padx=10, pady=(4, 10))

        self.tree = ttk.Treeview(
            tree_frame,
            columns=("name", "images"),
            show="tree headings",
            selectmode="browse",
        )
        self.tree.heading("#0", text="Иконка")
        self.tree.column("#0", width=56, anchor="center", stretch=False)
        self.tree.heading("name", text="Название")
        self.tree.column("name", width=330, anchor="w")
        self.tree.heading("images", text="Фото")
        self.tree.column("images", width=420, anchor="w")

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical",
                                  command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-1>", lambda _e: self.rename_item())

        detail = tk.Frame(middle, bg=Theme.PANEL,
                          highlightbackground=Theme.BORDER,
                          highlightthickness=1, width=280)
        detail.pack(side="left", fill="y", padx=(12, 0))
        detail.pack_propagate(False)

        tk.Label(detail, text="ВЫБРАННЫЙ ПРЕДМЕТ", bg=Theme.PANEL,
                 fg=Theme.MUTED, font=("Segoe UI", 8, "bold")).pack(
            anchor="w", padx=14, pady=(12, 4))

        self.detail_icon = tk.Label(detail, text="—", bg=Theme.PANEL,
                                    fg=Theme.MUTED, font=("Segoe UI", 9))
        self.detail_icon.pack(pady=6)

        self.detail_name = tk.Label(detail, text="", bg=Theme.PANEL,
                                    fg=Theme.TEXT, wraplength=240,
                                    font=("Segoe UI", 12, "bold"))
        self.detail_name.pack(padx=14)

        self.detail_info = tk.Label(detail, text="", bg=Theme.PANEL,
                                    fg=Theme.MUTED, wraplength=240,
                                    justify="left", font=("Segoe UI", 9))
        self.detail_info.pack(padx=14, pady=(6, 0), anchor="w")

        buttons = tk.Frame(detail, bg=Theme.PANEL)
        buttons.pack(side="bottom", fill="x", padx=12, pady=12)

        themed_button(buttons, "Переименовать", self.rename_item) \
            .pack(fill="x", pady=3)
        themed_button(buttons, "+ Ещё фото", self.add_photo) \
            .pack(fill="x", pady=3)
        themed_button(buttons, "Удалить фото", self.remove_photo) \
            .pack(fill="x", pady=3)
        themed_button(buttons, "Объединить с…", self.merge_item) \
            .pack(fill="x", pady=3)
        themed_button(buttons, "Удалить предмет", self.delete_item) \
            .pack(fill="x", pady=3)

        self.status = tk.Label(self, text="", bg=Theme.BG, fg=Theme.MUTED,
                               anchor="w", font=("Segoe UI", 9))
        self.status.pack(fill="x", padx=20, pady=(0, 12))

        self.refresh_list()

    # ------------------------------------------------------------

    def current_item_name(self):
        selection = self.tree.selection()
        if not selection:
            return None
        return self.tree.item(selection[0], "tags") and \
            self.tree.item(selection[0], "tags")[0]

    def refresh_list(self, *_):
        self.reader.reload_items_db()
        selected = self.current_item_name()
        filter_text = self.search_var.get().strip().lower()

        self.tree.delete(*self.tree.get_children())

        for item in sorted(self.reader.items_db.get("items", []),
                           key=lambda entry: (entry.get("name", "") or "").lower()):
            name = item.get("name", "")

            if filter_text and filter_text not in name.lower():
                continue

            images = item_images_of(item)
            missing = sum(
                1 for path in images
                if not os.path.isfile(absolute_path(path))
            )

            icon_key = images[0] if images else ""
            photo = self.icon_for(images[0]) if images else None

            labels = []
            for index, path in enumerate(images, start=1):
                base = os.path.basename(path)
                if not os.path.isfile(absolute_path(path)):
                    base += " ✗"
                labels.append(base)

            self.tree.insert(
                "", "end",
                iid=name,
                text="",
                image=photo,
                values=(name, f"{len(images) - missing} шт: " + ", ".join(labels)),
                tags=(name,),
            )

        if selected:
            try:
                self.tree.selection_set(selected)
                self.tree.see(selected)
            except tk.TclError:
                pass

        total = len(self.reader.items_db.get("items", []))
        unknown = sum(
            1 for entry in self.reader.items_db.get("items", [])
            if (entry.get("name") or "").startswith("unknown_")
        )
        self.status.configure(
            text=f"Всего предметов: {total} | Неизвестных: {unknown}"
        )

    def icon_for(self, relative_path):
        cached = self._photo_cache.get(relative_path)
        if cached is not None:
            return cached

        full = absolute_path(relative_path)
        photo = load_photo_image(full, zoom_if_small=False) if full else None

        if photo is None:
            photo = tk.PhotoImage(width=1, height=1)

        self._photo_cache[relative_path] = photo
        return photo

    def on_select(self, *_):
        name = self.current_item_name()
        self.detail_name.configure(text=name or "—")

        if not name:
            self.detail_icon.configure(image="", text="—")
            self.detail_info.configure(text="")
            return

        item = self.reader.find_item_from_alias(name)
        images = item_images_of(item) if item else []

        photo = None
        if images:
            photo = self.icon_for(images[0])

        big = None
        if images and os.path.isfile(absolute_path(images[0])):
            big = load_photo_image(absolute_path(images[0]))

        if big is not None:
            self._photo_cache["__detail__" + name] = big
            self.detail_icon.configure(image=big, text="")
            self.detail_icon.image = big
        else:
            self.detail_icon.configure(image="", text="нет файла")

        lines = [f"Шаблонов: {len(images)}"]
        for path in images:
            exists = "✓" if os.path.isfile(absolute_path(path)) else "✗ потерян"
            lines.append(f"• {path}  [{exists}]")
        self.detail_info.configure(text="\n".join(lines))

    # ---------------- операции ----------------

    def add_item(self):
        dialog = ItemEditDialog(self, title="Новый предмет",
                                initial_name="", allow_empty_name=True)
        if dialog.result is None:
            return

        name, image_path = dialog.result

        if not name:
            messagebox.showwarning("Внимание", "Введите название предмета.",
                                   parent=self)
            return

        if not image_path:
            messagebox.showwarning(
                "Внимание", "Выберите изображение предмета.", parent=self
            )
            return

        ok, message = self.reader.add_item_manual(name, image_path)
        self.flash(message, ok)
        self.refresh_list()

    def rename_item(self):
        name = self.current_item_name()
        if not name:
            return

        dialog = ItemEditDialog(self, title="Переименовать предмет",
                                initial_name=name, allow_empty_name=False)
        if dialog.result is None:
            return

        new_name, _image = dialog.result

        if not new_name or new_name == name:
            return

        ok, message = self.reader.rename_item(name, new_name)
        self.flash(message, ok)
        self._photo_cache.clear()
        self.refresh_list()

    def add_photo(self):
        name = self.current_item_name()
        if not name:
            return

        path = filedialog.askopenfilename(
            parent=self, title="Выберите фото предмета",
            filetypes=[("Изображения", "*.png *.jpg *.jpeg *.bmp *.webp"),
                       ("Все файлы", "*.*")]
        )
        if not path:
            return

        ok, message = self.reader.add_item_image(name, path)
        self.flash(message, ok)
        self._photo_cache.pop(next(iter(
            [p for p in self._photo_cache if isinstance(p, str)] or [""]), ""), None)
        self._photo_cache.clear()
        self.refresh_list()

    def remove_photo(self):
        name = self.current_item_name()
        if not name:
            return

        item = self.reader.find_item_from_alias(name)
        images = item_images_of(item) if item else []

        if len(images) <= 1:
            messagebox.showwarning(
                "Внимание",
                "У предмета только одно фото. Чтобы удалить предмет целиком, "
                "используйте «Удалить предмет».",
                parent=self
            )
            return

        picker = PhotoPicker(self, images, name)
        if picker.chosen is None:
            return

        ok, message = self.reader.remove_item_image(name, picker.chosen)
        self.flash(message, ok)
        self._photo_cache.clear()
        self.refresh_list()

    def merge_item(self):
        name = self.current_item_name()
        if not name:
            return

        MergeDialogHandler = getattr(self, "_merge_with", None)
        dialog = MergeDialog(self, name)
        if dialog.chosen is None:
            return

        ok, message = self.reader.merge_items(name, dialog.chosen)
        self.flash(message, ok)
        self._photo_cache.clear()
        self.refresh_list()

    def delete_item(self):
        name = self.current_item_name()
        if not name:
            return

        if not messagebox.askyesno(
            "Подтверждение",
            f"Удалить предмет «{name}» и все его фото?",
            parent=self
        ):
            return

        ok, message = self.reader.delete_item(name)
        self.flash(message, ok)
        self._photo_cache.clear()
        self.refresh_list()

    def flash(self, message, ok=True):
        self.status.configure(
            text=message, fg=Theme.GREEN if ok else Theme.RED
        )
        self.app._log(message, Theme.GREEN if ok else Theme.RED)


class ItemEditDialog(tk.SimpleDialog):
    """Мини-диалог: название + выбор файла изображения."""

    def __init__(self, parent, title, initial_name="", allow_empty_name=False):
        self.result = None
        self.allow_empty_name = allow_empty_name
        self.image_path = tk.StringVar(value="")

        super().__init__(
            parent, title=title, text="Название и фото предмета:",
            font=("Segoe UI", 10)
        )

    def body(self, master):
        master.master.configure(bg=Theme.PANEL)
        master.configure(bg=Theme.PANEL)

        frame = tk.Frame(master, bg=Theme.PANEL)
        frame.pack(fill="both", expand=True, padx=8, pady=4)

        tk.Label(frame, text="Название:", bg=Theme.PANEL, fg=Theme.TEXT,
                 font=("Segoe UI", 10)).grid(row=0, column=0, sticky="w", pady=4)

        self.name_entry = tk.Entry(
            frame, bg=Theme.PANEL_2, fg=Theme.TEXT,
            insertbackground=Theme.TEXT, relief="flat",
            font=("Segoe UI", 11), width=36
        )
        self.name_entry.grid(row=0, column=1, sticky="we", padx=8, ipady=6)
        self.name_entry.insert(0, initial_name)

        def focus_name():
            self.name_entry.focus_set()
            self.name_entry.icursor("end")

        self.after(80, focus_name)

        tk.Label(frame, text="Фото:", bg=Theme.PANEL, fg=Theme.TEXT,
                 font=("Segoe UI", 10)).grid(row=1, column=0, sticky="w", pady=4)

        path_entry = tk.Entry(
            frame, textvariable=self.image_path,
            bg=Theme.PANEL_2, fg=Theme.TEXT, insertbackground=Theme.TEXT,
            relief="flat", font=("Segoe UI", 9), width=36
        )
        path_entry.grid(row=1, column=1, sticky="we", padx=(8, 0))

        browse = themed_button(frame, "Обзор…", self.pick_file)
        browse.grid(row=1, column=2, padx=4)

        self.icon_preview = tk.Label(frame, text="", bg=Theme.PANEL, width=10)
        self.icon_preview.grid(row=0, column=2, rowspan=1, padx=4)

        return frame

    def pick_file(self):
        path = filedialog.askopenfilename(
            parent=self, title="Выберите изображение",
            filetypes=[("Изображения", "*.png *.jpg *.jpeg *.bmp *.webp"),
                       ("Все файлы", "*.*")]
        )
        if path:
            self.image_path.set(path)
            photo = load_photo_image(path, zoom_if_small=False)
            if photo is not None:
                self.icon_preview.image = photo
                self.icon_preview.configure(image=photo)

    def apply(self):
        name = self.name_entry.get().strip()
        self.result = (name, self.image_path.get().strip() or None)
        tk.SimpleDialog.destroy(self)


class PhotoPicker(tk.Toplevel):
    """Выбор одного из фото предмета для удаления."""

    def __init__(self, parent, images, item_name):
        super().__init__(parent)
        self.title(f"Фото предмета «{item_name}»")
        self.configure(bg=Theme.BG)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.chosen = None
        self._photos = []

        grid = tk.Frame(self, bg=Theme.BG)
        grid.pack(padx=18, pady=18)

        for index, path in enumerate(images):
            full = absolute_path(path)
            photo = load_photo_image(full) if full else None
            tile = tk.Label(
                grid, bg=Theme.PANEL, width=8, height=5,
                relief="flat", cursor="hand2",
                highlightbackground=Theme.BORDER, highlightthickness=1,
            )
            tile.grid(row=index // 4, column=index % 4, padx=6, pady=6)

            if photo is not None:
                self._photos.append(photo)
                tile.configure(image=photo)
                tile.image = photo
            else:
                tile.configure(text="потерян", fg=Theme.RED)

            tile.bind(
                "<Button-1>",
                lambda _event, target=path: self.choose(target)
            )

        tk.Label(self, text="Кликните по фото, которое нужно удалить.",
                 bg=Theme.BG, fg=Theme.MUTED, font=("Segoe UI", 9)).pack(pady=(0, 14))

    def choose(self, path):
        self.chosen = path
        self.grab_release()
        self.destroy()


class MergeDialog(tk.Toplevel):
    """Выбор предмета-цели для объединения."""

    def __init__(self, parent, source_name):
        super().__init__(parent)
        self.title("Объединить с…")
        self.geometry("420x480")
        self.configure(bg=Theme.BG)
        self.transient(parent)
        self.grab_set()

        self.chosen = None
        self.source_name = source_name

        tk.Label(self, text=f"Все фото «{source_name}» будут перенесены\n"
                            "в выбранный предмет, а сам он удалён из базы.",
                 bg=Theme.BG, fg=Theme.MUTED, font=("Segoe UI", 9)).pack(pady=(14, 6))

        list_frame = tk.Frame(self, bg=Theme.PANEL,
                              highlightbackground=Theme.BORDER,
                              highlightthickness=1)
        list_frame.pack(fill="both", expand=True, padx=18, pady=6)

        self.listbox = tk.Listbox(
            list_frame, bg=Theme.PANEL, fg=Theme.TEXT,
            selectbackground="#29304a", relief="flat", bd=0,
            font=("Segoe UI", 10), activestyle="none",
        )
        self.listbox.pack(fill="both", expand=True, padx=6, pady=6)

        reader = parent.reader
        reader.reload_items_db()
        self.names = [
            entry.get("name") for entry in reader.items_db.get("items", [])
            if entry.get("name") and entry.get("name") != source_name
        ]
        for name in sorted(self.names, key=str.lower):
            self.listbox.insert("end", name)

        buttons = tk.Frame(self, bg=Theme.BG)
        buttons.pack(pady=12)
        themed_button(buttons, "Объединить", self.confirm, accent=True) \
            .pack(side="left", padx=6)
        themed_button(buttons, "Отмена", self.cancel).pack(side="left", padx=6)

    def confirm(self):
        selection = self.listbox.curselection()
        if selection:
            self.chosen = self.listbox.get(selection[0])
        self.grab_release()
        self.destroy()

    def cancel(self):
        self.chosen = None
        self.grab_release()
        self.destroy()


# ============================================================
# ОКНО НАСТРОЙКИ DISCORD ИЗ ИНТЕРФЕЙСА
# ============================================================

class DiscordSettingsDialog(tk.Toplevel):
    """Настройка вебхука и формата сообщений Discord из интерфейса."""

    MESSAGE_FIELDS = [
        ("header", "Заголовок (можно с **Markdown**)"),
        ("scanned_chests", "Строка «сундуков»"),
        ("last_scan", "Строка «последнее сканирование»"),
        ("items_title", "Заголовок списка предметов"),
        ("columns", "Шапка таблицы"),
        ("item", "Формат строки предмета"),
        ("unknown_title", "Заголовок unknown-раздела"),
        ("unknown_item", "Формат unknown-строки"),
    ]

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.reader = app.reader

        self.title("Настройка Discord")
        self.geometry("860x640")
        self.minsize(720, 520)
        self.configure(bg=Theme.BG)
        self.transient(app.root)

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=16, pady=(14, 4))

        webhook_tab = tk.Frame(notebook, bg=Theme.BG)
        format_tab = tk.Frame(notebook, bg=Theme.BG)
        notebook.add(webhook_tab, text="  Подключение  ")
        notebook.add(format_tab, text="  Формат сообщения  ")

        self.build_webhook_tab(webhook_tab)
        self.build_format_tab(format_tab)

        footer = tk.Frame(self, bg=Theme.BG)
        footer.pack(fill="x", padx=16, pady=(6, 14))

        self.status = tk.Label(footer, text="", bg=Theme.BG, fg=Theme.MUTED,
                               font=("Segoe UI", 9))
        self.status.pack(side="left")

        themed_button(footer, "Сохранить всё", self.save_all, accent=True) \
            .pack(side="right")
        themed_button(footer, "Закрыть", self.close_dialog).pack(
            side="right", padx=(0, 8))

    # -------------------- вкладка подключения -------------------

    def build_webhook_tab(self, tab):
        card = tk.Frame(tab, bg=Theme.PANEL,
                        highlightbackground=Theme.BORDER, highlightthickness=1)
        card.pack(fill="x", pady=14, padx=6)

        tk.Label(card, text="Webhook URL", bg=Theme.PANEL, fg=Theme.MUTED,
                 font=("Segoe UI", 9, "bold")).grid(row=0, column=0,
                 sticky="w", padx=16, pady=(14, 4))

        current = ""
        if os.path.exists(DISCORD_WEBHOOK_FILE):
            current = load_json(DISCORD_WEBHOOK_FILE, {}).get("webhook_url", "")

        self.webhook_entry = tk.Entry(
            card, bg=Theme.PANEL_2, fg=Theme.TEXT, insertbackground=Theme.TEXT,
            relief="flat", font=("Segoe UI", 10), width=76
        )
        self.webhook_entry.grid(row=1, column=0, sticky="we", padx=16, pady=(0, 10))
        self.webhook_entry.insert(0, current)

        self.connection_status = tk.Label(card, text="", bg=Theme.PANEL,
                                          fg=Theme.MUTED, font=("Segoe UI", 9),
                                          wraplength=640, justify="left")
        self.connection_status.grid(row=2, column=0, sticky="w", padx=16,
                                    pady=(0, 14))

        buttons = tk.Frame(card, bg=Theme.PANEL)
        buttons.grid(row=1, column=1, padx=(0, 16))
        themed_button(buttons, "Проверить", self.test_connection).pack(
            pady=(0, 6))

        hint = tk.Label(
            tab, bg=Theme.BG, fg=Theme.MUTED, justify="left",
            font=("Segoe UI", 9),
            text=(
                "Discord: шестерёнка канала → Интеграции → Вебхуки → Новый вебхук.\n"
                "URL сохраняется в профиле («" + ACTIVE_PROFILE + "») и никуда "
                "не отправляется без вашей команды."
            )
        )
        hint.pack(anchor="w", padx=8)

    def validate_url(self):
        url = self.webhook_entry.get().strip()

        if url and not url.startswith((
            "https://discord.com/api/webhooks/",
            "https://discordapp.com/api/webhooks/",
        )):
            return None, "URL должен начинаться с https://discord.com/api/webhooks/"

        return url, None

    def test_connection(self):
        url, error = self.validate_url()

        if error or not url:
            self.connection_status.configure(text=error or "Введите URL.",
                                             fg=Theme.RED)
            return

        self.connection_status.configure(text="Отправка тестового сообщения…",
                                         fg=Theme.MUTED)

        def worker():
            ok, message = self.reader.send_test_to_webhook(url)
            self.after(0, lambda: self.connection_status.configure(
                text=message, fg=Theme.GREEN if ok else Theme.RED
            ))

        threading.Thread(target=worker, daemon=True).start()

    # -------------------- вкладка формата -----------------------

    def build_format_tab(self, tab):
        container = tk.Frame(tab, bg=Theme.BG)
        container.pack(fill="both", expand=True, padx=6, pady=8)

        canvas = tk.Canvas(container, bg=Theme.BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=Theme.BG)

        inner.bind(
            "<Configure>",
            lambda _e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        discord_message = dict(self.reader.config.get("discord_message", {}))
        self.format_entries = {}

        info = tk.Label(
            inner, bg=Theme.BG, fg=Theme.MUTED, justify="left",
            font=("Segoe UI", 9),
            text="Доступные подстановки: {chests} {last_scan} {items} "
                 "{unknowns} {icon} {name} {total} {stacks}"
        )
        info.pack(anchor="w", pady=(0, 10))

        for key, label in self.MESSAGE_FIELDS:
            row = tk.Frame(inner, bg=Theme.BG)
            row.pack(fill="x", pady=3)

            tk.Label(row, text=label, bg=Theme.BG, fg=Theme.MUTED, width=28,
                     anchor="w", font=("Segoe UI", 9)).pack(side="left")

            entry = tk.Entry(
                row, bg=Theme.PANEL_2, fg=Theme.TEXT,
                insertbackground=Theme.TEXT, relief="flat",
                font=("Segoe UI", 10)
            )
            entry.pack(side="left", fill="x", expand=True, padx=8, ipady=5)
            value = discord_message.get(key, "")
            display = value.replace("\n", "\\n")
            entry.insert(0, display)
            self.format_entries[key] = entry

        numbers = tk.Frame(inner, bg=Theme.BG)
        numbers.pack(fill="x", pady=(12, 3))

        self.stack_size_var = tk.StringVar(
            value=str(discord_message.get("stack_size", 64)))
        self.max_items_var = tk.StringVar(
            value=str(self.reader.config.get("discord_max_items_in_message", 25)))

        for label, variable, width in (
            ("Размер стака:", self.stack_size_var, 8),
            ("Макс. строк в сообщении:", self.max_items_var, 8),
        ):
            tk.Label(numbers, text=label, bg=Theme.BG, fg=Theme.MUTED,
                     font=("Segoe UI", 9)).pack(side="left", padx=(0, 6))
            entry = tk.Entry(numbers, textvariable=variable, width=width,
                             bg=Theme.PANEL_2, fg=Theme.TEXT,
                             insertbackground=Theme.TEXT, relief="flat",
                             font=("Segoe UI", 10))
            entry.pack(side="left", padx=(0, 18), ipady=4)

        preview_box = tk.LabelFrame(
            inner, text=" Предпросмотр ", bg=Theme.PANEL, fg=Theme.MUTED,
            font=("Segoe UI", 9, "bold"),
            highlightbackground=Theme.BORDER, highlightcolor=Theme.BORDER,
        )
        preview_box.pack(fill="both", expand=True, pady=(14, 4), minsize=140)

        self.preview = tk.Text(
            preview_box, bg=Theme.PANEL, fg="#cbd2dc", relief="flat", bd=0,
            font=("Consolas", 9), wrap="word", height=8
        )
        self.preview.pack(fill="both", expand=True, padx=8, pady=8)

        themed_button(inner, "Обновить предпросмотр",
                      self.update_preview).pack(anchor="w", pady=(6, 10))

        self.update_preview()

    def collect_format(self):
        discord_message = dict(self.reader.config.get("discord_message", {}))

        for key, entry in self.format_entries.items():
            discord_message[key] = entry.get().replace("\\n", "\n")

        try:
            discord_message["stack_size"] = int(self.stack_size_var.get() or 64)
        except ValueError:
            pass

        return discord_message

    def update_preview(self):
        discord_message = self.collect_format()
        saved = self.reader.config.get("discord_message")
        self.reader.config["discord_message"] = discord_message

        try:
            data = load_json(RESULTS_FILE, {"chests": []})
            chests = data.get("chests", []) if isinstance(data, dict) else []
            if not chests:
                chests = [{
                    "timestamp": datetime.now(ZoneInfo("Europe/Moscow"))
                                 .strftime("%d.%m.%Y %H:%M:%S"),
                    "totals": {"Золотой слиток": 128, "Алмаз": 57},
                    "unknown_items": ["unknown_a1b2c3d4e5"],
                }]
            text = self.reader.build_discord_message(chests)
        finally:
            if saved is not None:
                self.reader.config["discord_message"] = saved

        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text)

    # -------------------- сохранение -----------------------------

    def save_all(self):
        url, error = self.validate_url()

        if error:
            self.connection_status.configure(text=error, fg=Theme.RED)
            messagebox.showwarning("Внимание", error, parent=self)
            return

        save_json(DISCORD_WEBHOOK_FILE, {"webhook_url": url or ""})

        self.reader.config["discord_message"] = self.collect_format()

        try:
            self.reader.config["discord_max_items_in_message"] = int(
                self.max_items_var.get() or 25
            )
        except ValueError:
            pass

        self.reader.save_config()

        self.app._log("Настройки Discord сохранены.", Theme.GREEN)
        self.status.configure(text="Сохранено ✓", fg=Theme.GREEN)

    def close_dialog(self):
        self.destroy()


# ============================================================
# ГЛАВНОЕ ОКНО
# ============================================================

class ScannerGUI:
    """Современное тёмное окно управления сканером."""

    BG = "#101318"
    PANEL = "#171b22"
    PANEL_2 = "#1d232c"
    BORDER = "#2a313c"
    TEXT = "#f2f4f7"
    MUTED = "#8f9aaa"
    ACCENT = "#5865f2"
    ACCENT_HOVER = "#6975ff"
    GREEN = "#35c98b"
    RED = "#f25f67"
    YELLOW = "#f0b84b"

    def __init__(self, reader):
        self.reader = reader
        self.root = tk.Tk()
        self.root.title("Chest Scanner")
        self.root.geometry("980x680")
        self.root.minsize(860, 600)
        self.root.configure(bg=self.BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.log_queue = Queue()
        self.busy = False
        self.running = True

        self.items_dialog = None
        self.discord_dialog = None

        self.reader.unknown_item_prompt = self._prompt_unknown_item

        self._setup_style()
        self._build_ui()
        self._refresh_stats()
        self._poll_logs()
        self._keyboard_loop()

        # При первом запуске профиля автоматически запускаем мастер.
        if not os.path.exists(SETUP_DONE_FLAG):
            self.root.after(400, self.open_setup_wizard)

    # ------------------------------------------------------------
    # ПРОФИЛИ И ВСПОМОГАТЕЛЬНЫЕ ОКНА
    # ------------------------------------------------------------

    def restart_with_profile(self, profile_name):
        """Перезапускает программу с выбранным активным профилем."""
        self._log(f"Перезапуск в профиле «{profile_name}»…")
        self.running = False

        try:
            python_executable = (
                sys.executable if getattr(sys, "frozen", False)
                else sys.executable
            )
            script = os.path.abspath(__file__)

            creation_flags = 0
            if os.name == "nt":
                creation_flags = subprocess.DETACHED_PROCESS | \
                    subprocess.CREATE_NEW_PROCESS_GROUP

            # Новый процесс обязан унаследовать чистое окружение: иначе
            # он получит переменную CHEST_PROFILE от текущего запуска и
            # проигнорирует только что сохранённый profiles/active_profile.txt.
            child_env = os.environ.copy()
            child_env.pop(PROFILE_ENV_VAR, None)

            subprocess.Popen(
                [python_executable, script],
                cwd=BASE_DIR,
                env=child_env,
                creationflags=creation_flags,
                close_fds=True,
            )
        except OSError as error:
            messagebox.showerror(
                "Ошибка",
                f"Не удалось перезапустить программу: {error}",
                parent=self.root,
            )
            return

        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def switch_profile_interactive(self):
        profiles = get_profiles()
        window = tk.Toplevel(self.root)
        window.title("Сменить профиль")
        window.configure(bg=self.BG)
        window.resizable(False, False)
        window.transient(self.root)
        window.grab_set()

        tk.Label(window, text="Активный профиль:", bg=self.BG, fg=self.MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=18,
                 pady=(16, 4))

        combo = ttk.Combobox(window, values=profiles, state="readonly",
                             width=32, font=("Segoe UI", 11))
        combo.set(self.reader.active_profile)
        combo.pack(padx=18, anchor="w")

        entry = tk.Entry(window, bg=self.PANEL_2, fg=self.TEXT,
                         insertbackground=self.TEXT, relief="flat",
                         font=("Segoe UI", 11), width=34)
        entry.pack(padx=18, pady=(14, 4), anchor="w")
        entry.insert(0, "Новое имя профиля")

        def select_entry(_event=None):
            if entry.get() == "Новое имя профиля":
                entry.delete(0, "end")
                entry.icursor("end")

        entry.bind("<FocusIn>", select_entry)

        buttons = tk.Frame(window, bg=self.BG)
        buttons.pack(fill="x", padx=18, pady=16)

        def do_switch():
            selected = combo.get().strip()
            if selected and selected != self.reader.active_profile:
                set_active_profile(selected)
                window.destroy()
                self.restart_with_profile(selected)

        def do_create():
            name = sanitize_profile_name(entry.get())
            if not name or name == "Новое имя профиля":
                messagebox.showwarning("Внимание", "Введите имя профиля.",
                                       parent=window)
                return
            if os.path.exists(os.path.join(PROFILES_DIR, name)):
                messagebox.showwarning("Внимание",
                                       f"Профиль «{name}» уже существует.",
                                       parent=window)
                return
            set_active_profile(name)
            window.destroy()
            self.restart_with_profile(name)

        themed_button(buttons, "Создать новый", do_create).pack(side="left")
        themed_button(buttons, "Переключить", do_switch, accent=True) \
            .pack(side="right")

    def open_items_dialog(self):
        if self.items_dialog is not None and self.items_dialog.winfo_exists():
            self.items_dialog.lift()
            self.items_dialog.focus_force()
            return
        self.items_dialog = ItemsDatabaseDialog(self)

    def open_discord_dialog(self):
        if self.discord_dialog is not None and self.discord_dialog.winfo_exists():
            self.discord_dialog.lift()
            self.discord_dialog.focus_force()
            return
        self.discord_dialog = DiscordSettingsDialog(self)

    def open_setup_wizard(self):
        wizard = SetupWizard(self)
        wizard.wait_window()
        self._refresh_stats()

    def _setup_style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Treeview",
            background=self.PANEL,
            fieldbackground=self.PANEL,
            foreground=self.TEXT,
            rowheight=30,
            borderwidth=0,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Treeview.Heading",
            background=self.PANEL_2,
            foreground=self.MUTED,
            relief="flat",
            font=("Segoe UI", 9, "bold"),
        )
        style.map("Treeview", background=[("selected", "#29304a")])

    def _button(self, parent, text, command, accent=False):
        bg = self.ACCENT if accent else self.PANEL_2
        active = self.ACCENT_HOVER if accent else "#252c36"
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=self.TEXT,
            activebackground=active,
            activeforeground=self.TEXT,
            relief="flat",
            bd=0,
            padx=18,
            pady=11,
            cursor="hand2",
            font=("Segoe UI", 10, "bold"),
        )
        button.bind("<Enter>", lambda e: button.configure(bg=active))
        button.bind("<Leave>", lambda e: button.configure(bg=bg))
        return button

    def _card(self, parent, title, value, color=None):
        frame = tk.Frame(parent, bg=self.PANEL, highlightbackground=self.BORDER, highlightthickness=1)
        tk.Label(frame, text=title.upper(), bg=self.PANEL, fg=self.MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=16, pady=(13, 2))
        label = tk.Label(frame, text=value, bg=self.PANEL, fg=color or self.TEXT,
                         font=("Segoe UI", 22, "bold"))
        label.pack(anchor="w", padx=16, pady=(0, 13))
        return frame, label

    def _build_ui(self):
        # Header
        header = tk.Frame(self.root, bg=self.BG)
        header.pack(fill="x", padx=28, pady=(24, 14))

        left = tk.Frame(header, bg=self.BG)
        left.pack(side="left")
        tk.Label(left, text="Chest Scanner", bg=self.BG, fg=self.TEXT,
                 font=("Segoe UI", 24, "bold")).pack(anchor="w")
        tk.Label(left, text="Распознавание сундуков Minecraft", bg=self.BG, fg=self.MUTED,
                 font=("Segoe UI", 10)).pack(anchor="w", pady=(2, 0))

        self.status_dot = tk.Label(header, text="●  ГОТОВ", bg=self.BG, fg=self.GREEN,
                                   font=("Segoe UI", 10, "bold"))
        self.status_dot.pack(side="right", pady=10)

        # Main buttons
        actions = tk.Frame(self.root, bg=self.BG)
        actions.pack(fill="x", padx=28, pady=(0, 8))
        self._button(actions, "Сканировать", lambda: self._run("Сканирование", self._scan), True).pack(side="left", padx=(0, 8))
        self._button(actions, "База предметов", self.open_items_dialog).pack(side="left", padx=4)
        self._button(actions, "Настройки Discord", self.open_discord_dialog).pack(side="left", padx=4)
        self._button(actions, "Отправить отчёт", lambda: self._run("Отправка в Discord", self._discord)).pack(side="left", padx=4)
        self._button(actions, "Мастер", self.open_setup_wizard).pack(side="left", padx=4)
        self._button(actions, f"Профиль: {self.reader.active_profile}", self.switch_profile_interactive).pack(side="left", padx=4)
        self._button(actions, "Выход", self.close).pack(side="right")

        second_row = tk.Frame(self.root, bg=self.BG)
        second_row.pack(fill="x", padx=28, pady=(0, 14))
        self._button(second_row, "Сетка F7", lambda: self._run("Отладка сетки", self.reader.show_grid_debug)).pack(side="left")
        self._button(second_row, "Цифры F6", lambda: self._run("Отладка цифр", self.reader.save_quantity_debug)).pack(side="left", padx=8)

        # Stats
        stats = tk.Frame(self.root, bg=self.BG)
        stats.pack(fill="x", padx=28, pady=(0, 18))
        for i in range(4):
            stats.grid_columnconfigure(i, weight=1)
        self.cards = {}
        for i, (key, title, value, color) in enumerate([
            ("chests", "Сундуков", "0", None),
            ("items", "Предметов", "0", None),
            ("unique", "Уникальных", "0", self.ACCENT_HOVER),
            ("unknown", "Неизвестных", "0", self.YELLOW),
        ]):
            card, label = self._card(stats, title, value, color)
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 6, 6 if i < 3 else 0))
            self.cards[key] = label

        # Content split
        content = tk.Frame(self.root, bg=self.BG)
        content.pack(fill="both", expand=True, padx=28, pady=(0, 18))

        log_panel = tk.Frame(content, bg=self.PANEL, highlightbackground=self.BORDER, highlightthickness=1)
        log_panel.pack(fill="both", expand=True)
        tk.Label(log_panel, text="ЖУРНАЛ", bg=self.PANEL, fg=self.MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=16, pady=(14, 8))

        self.log = tk.Text(log_panel, bg=self.PANEL, fg="#cbd2dc", insertbackground=self.TEXT,
                           relief="flat", bd=0, wrap="word", font=("Consolas", 9), padx=16, pady=4)
        self.log.pack(fill="both", expand=True, side="left")
        scroll = tk.Scrollbar(log_panel, command=self.log.yview)
        scroll.pack(side="right", fill="y", padx=(0, 6), pady=(0, 8))
        self.log.configure(yscrollcommand=scroll.set)

        footer = tk.Frame(self.root, bg=self.BG)
        footer.pack(fill="x", padx=28, pady=(0, 18))
        tk.Label(footer, text="F6  цифры    F7  сетка    F8  сканировать    F9  Discord    F10  выход",
                 bg=self.BG, fg=self.MUTED, font=("Segoe UI", 9)).pack(side="left")
        self.last_scan_label = tk.Label(footer, text="Последнее действие: —", bg=self.BG, fg=self.MUTED,
                                        font=("Segoe UI", 9))
        self.last_scan_label.pack(side="right")

        self._log("Интерфейс запущен.")
        self._log("Горячие клавиши F6–F10 активны.")

    def _prompt_unknown_item(self, unknown_name, image_path):
        """Показывает окно для названия нового предмета вместе с его иконкой."""
        result = {"name": ""}
        done = threading.Event()

        def show_dialog():
            dialog = tk.Toplevel(self.root)
            dialog.title("Новый предмет")
            dialog.geometry("640x340")
            dialog.resizable(False, False)
            dialog.configure(bg=self.BG)
            dialog.transient(self.root)
            dialog.grab_set()

            tk.Label(
                dialog, text="Найден новый предмет", bg=self.BG, fg=self.TEXT,
                font=("Segoe UI", 17, "bold")
            ).pack(anchor="w", padx=24, pady=(22, 4))

            body = tk.Frame(dialog, bg=self.BG)
            body.pack(fill="both", expand=True, padx=24, pady=(10, 0))

            # Блок с иконкой обнаруженного предмета.
            icon_frame = tk.Frame(
                body,
                bg=self.PANEL,
                highlightbackground=self.BORDER,
                highlightthickness=1,
                width=140,
                height=140,
            )
            icon_frame.pack(side="left", anchor="n", padx=(0, 20))
            icon_frame.pack_propagate(False)

            icon_label = tk.Label(
                icon_frame,
                text="Нет\nиконки",
                bg=self.PANEL,
                fg=self.MUTED,
                font=("Segoe UI", 9),
            )
            icon_label.pack(expand=True)

            # PNG сохраняется add_unknown_item() до открытия этого окна,
            # поэтому здесь можно сразу показать именно найденную иконку.
            try:
                if image_path and os.path.isfile(image_path):
                    icon_image = tk.PhotoImage(file=image_path)

                    # Иконки неизвестных предметов сохраняются в 32x32.
                    # Увеличиваем до 96x96 для удобного просмотра.
                    if icon_image.width() <= 48 and icon_image.height() <= 48:
                        icon_image = icon_image.zoom(3, 3)

                    icon_label.configure(image=icon_image, text="")
                    # Tkinter требует хранить ссылку на PhotoImage,
                    # иначе изображение может исчезнуть после создания Label.
                    icon_label.image = icon_image
            except Exception as error:
                print(f"[ПРЕДМЕТ] Не удалось показать иконку: {error}")

            info = tk.Frame(body, bg=self.BG)
            info.pack(side="left", fill="both", expand=True)

            tk.Label(
                info, text=f"Шаблон: {unknown_name}", bg=self.BG, fg=self.MUTED,
                font=("Segoe UI", 9)
            ).pack(anchor="w")

            tk.Label(
                info, text="Введите название предмета:", bg=self.BG, fg=self.TEXT,
                font=("Segoe UI", 10)
            ).pack(anchor="w", pady=(24, 6))

            entry = tk.Entry(
                info, bg=self.PANEL_2, fg=self.TEXT, insertbackground=self.TEXT,
                relief="flat", font=("Segoe UI", 11)
            )
            entry.pack(fill="x", ipady=8)
            entry.focus_set()

            buttons = tk.Frame(dialog, bg=self.BG)
            buttons.pack(fill="x", padx=24, pady=18)

            def finish():
                result["name"] = entry.get().strip()
                try:
                    dialog.grab_release()
                except tk.TclError:
                    pass
                dialog.destroy()
                done.set()

            tk.Button(
                buttons, text="Сохранить", command=finish, bg=self.ACCENT, fg=self.TEXT,
                activebackground=self.ACCENT_HOVER, activeforeground=self.TEXT,
                relief="flat", bd=0, padx=18, pady=8, font=("Segoe UI", 10, "bold")
            ).pack(side="right")
            tk.Button(
                buttons, text="Оставить unknown", command=finish, bg=self.PANEL_2, fg=self.TEXT,
                activebackground=self.BORDER, activeforeground=self.TEXT,
                relief="flat", bd=0, padx=14, pady=8, font=("Segoe UI", 9)
            ).pack(side="right", padx=(0, 8))

            dialog.bind("<Return>", lambda _event: finish())
            dialog.bind("<Escape>", lambda _event: finish())
            dialog.protocol("WM_DELETE_WINDOW", finish)

        self.root.after(0, show_dialog)
        done.wait()
        return result["name"]

    def _scan(self):
        result = self.reader.scan_chest(force=True)
        if result is not None:
            self._log("✓ Сундук успешно отсканирован.", self.GREEN)
        else:
            self._log("Сундук не найден или изменений нет.", self.MUTED)
        self._refresh_stats()

    def _discord(self):
        success, message = self.reader.send_all_chests_to_discord()

        if success:
            self._log(f"✓ {message}", self.GREEN)
        else:
            self._log(f"✗ Discord: {message}", self.RED)
            messagebox.showerror(
                "Ошибка Discord",
                message
            )

        self._refresh_stats()

    def _run(self, title, func):
        if self.busy:
            self._log("Подождите: предыдущая операция ещё выполняется.", self.YELLOW)
            return

        def worker():
            self.busy = True
            self.root.after(0, lambda: self.status_dot.configure(text="●  РАБОТАЕТ", fg=self.YELLOW))
            self._log(f"▶ {title}...")
            try:
                func()
                self._log(f"✓ {title}: готово.", self.GREEN)
            except Exception as error:
                self._log(f"✕ {title}: {type(error).__name__}: {error}", self.RED)
            finally:
                self.busy = False
                self.root.after(0, lambda: self.status_dot.configure(text="●  ГОТОВ", fg=self.GREEN))
                self.root.after(0, self._refresh_stats)

        threading.Thread(target=worker, daemon=True).start()

    def _keyboard_loop(self):
        if not self.running:
            return
        try:
            if keyboard.is_pressed("f6"):
                self._run("Отладка цифр", self.reader.save_quantity_debug)
                time.sleep(0.35)
            elif keyboard.is_pressed("f7"):
                self._run("Отладка сетки", self.reader.show_grid_debug)
                time.sleep(0.35)
            elif keyboard.is_pressed("f8"):
                self._run("Сканирование", self._scan)
                time.sleep(0.35)
            elif keyboard.is_pressed("f9"):
                self._run("Отправка в Discord", self._discord)
                time.sleep(0.35)
            elif keyboard.is_pressed("f10"):
                self.close()
                return
        except Exception as error:
            self._log(f"Ошибка горячей клавиши: {error}", self.RED)
        self.root.after(80, self._keyboard_loop)

    def _refresh_stats(self):
        try:
            data = load_json(RESULTS_FILE, {"chests": []})
            chests = data.get("chests", []) if isinstance(data, dict) else []
            totals = {}
            unknown = set()
            for chest in chests:
                for name, qty in (chest.get("totals", {}) or {}).items():
                    totals[name] = totals.get(name, 0) + int(qty or 0)
                unknown.update(chest.get("unknown_items", []) or [])
            self.cards["chests"].configure(text=str(len(chests)))
            self.cards["items"].configure(text=f"{sum(totals.values()):,}".replace(",", " "))
            self.cards["unique"].configure(text=str(len(totals)))
            self.cards["unknown"].configure(text=str(len(unknown)))
        except Exception:
            pass

    def _log(self, message, color=None):
        stamp = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%H:%M:%S")
        self.log_queue.put((f"[{stamp}] {message}", color))

    def _poll_logs(self):
        try:
            while True:
                message, color = self.log_queue.get_nowait()
                self.log.insert("end", message + "\n")
                if color:
                    tag = f"c{abs(hash(color))}"
                    self.log.tag_configure(tag, foreground=color)
                    start = self.log.index("end-2l linestart")
                    end = self.log.index("end-1l lineend")
                    self.log.tag_add(tag, start, end)
                self.log.see("end")
                self.last_scan_label.configure(text=f"Последнее действие: {message[10:]}")
        except Empty:
            pass
        if self.running:
            self.root.after(100, self._poll_logs)

    def close(self):
        if not self.running:
            return
        self.running = False
        self._log("Программа остановлена.")
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self):
        self.root.mainloop()


# ============================================================
# ЗАПУСК
# ============================================================

def main():
    reader = ChestReader()
    app = ScannerGUI(reader)
    app.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as main_error:
        _show_startup_error(main_error)
        raise
