import os
import sys
import subprocess
import hashlib
import re
import unicodedata
import shutil

# ============================================================
# AUTOMATIC .venv CREATION AND RESTART
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
    with open(_REQUIREMENTS_FILE, "rb") as f:
        digest.update(f.read())
    return digest.hexdigest()


def _read_saved_requirements_hash():
    try:
        with open(_REQUIREMENTS_HASH_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except (OSError, UnicodeError):
        return None


def _write_saved_requirements_hash(value):
    os.makedirs(_VENV_DIR, exist_ok=True)
    with open(_REQUIREMENTS_HASH_FILE, "w", encoding="utf-8") as f:
        f.write(value)


def _run_setup_command(command):
    kwargs = {
        "cwd": _BASE_DIR_FOR_VENV,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    result = subprocess.run(command, **kwargs)
    if result.returncode != 0:
        log_file = os.path.join(_BASE_DIR_FOR_VENV, "venv_setup.log")
        try:
            with open(log_file, "w", encoding="utf-8") as f:
                f.write(result.stdout or "")
        except OSError:
            pass
        raise RuntimeError(f"Failed to prepare .venv. Details: {log_file}")


def _show_startup_error(error):
    log_file = os.path.join(_BASE_DIR_FOR_VENV, "startup_error.log")
    message = f"{type(error).__name__}: {error}"
    try:
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(message)
    except OSError:
        pass

    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                message + f"\n\nDetails: {log_file}",
                "Chest Scanner startup error",
                0x10,
            )
        except Exception:
            pass


def _ensure_venv_and_restart():
    if _venv_is_active():
        return

    venv_python, venv_pythonw = _venv_python_paths()
    setup_python = sys.executable

    if os.name == "nt" and os.path.basename(setup_python).lower() == "pythonw.exe":
        candidate = os.path.join(os.path.dirname(setup_python), "python.exe")
        if os.path.isfile(candidate):
            setup_python = candidate

    if not os.path.isfile(venv_python):
        _run_setup_command([setup_python, "-m", "venv", _VENV_DIR])

    if not os.path.isfile(venv_python):
        raise RuntimeError(".venv created, but python.exe not found inside it.")

    if os.name == "nt" and not os.path.isfile(venv_pythonw):
        _run_setup_command([venv_python, "-m", "venv", "--clear", _VENV_DIR])

    if os.name == "nt" and not os.path.isfile(venv_pythonw):
        raise RuntimeError(".venv damaged: pythonw.exe not found.")

    req_hash = _requirements_hash()
    if req_hash is not None and _read_saved_requirements_hash() != req_hash:
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
        _write_saved_requirements_hash(req_hash)

    if os.name == "nt" and os.path.isfile(venv_pythonw):
        process = subprocess.Popen(
            [venv_pythonw, os.path.abspath(__file__), *sys.argv[1:]],
            cwd=_BASE_DIR_FOR_VENV,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if process.pid:
            sys.exit(0)
        raise RuntimeError("Failed to start program via pythonw.exe.")

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
    import threading
    import requests
    import numpy as np
    import pyautogui
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog, simpledialog
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from queue import Queue, Empty

    try:
        import keyboard
        KEYBOARD_AVAILABLE = True
    except Exception:
        keyboard = None
        KEYBOARD_AVAILABLE = False

    try:
        import winsound
        WINSOUND_AVAILABLE = True
    except Exception:
        winsound = None
        WINSOUND_AVAILABLE = False

except Exception as import_error:
    _show_startup_error(import_error)
    raise

# ============================================================
# PATHS
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
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
DISCORD_MESSAGE_IDS_FILE = os.path.join(RESULTS_DIR, "discord_message_ids.json")
DISCORD_WEBHOOK_FILE = os.path.join(PROFILE_DIR, "discord_webhook.json")

DEFAULT_CONFIG = {
    "profile_name": "Default",
    "setup_complete": False,
    "reference_screen_size": [1920, 1080],
    "chest_template": "assets/chest_open.png",
    "empty_slot_template": "assets/empty_slot.png",
    "chest_grid": {
        "x": 798,
        "y": 351,
        "width": 324,
        "height": 216,
        "columns": 9,
        "rows": 6,
    },
    "icon_roi_in_slot": [0.10, 0.08, 0.80, 0.67],
    "count_roi_in_slot": [0.30, 0.40, 0.67, 0.58],
    "chest_template_threshold": 0.82,
    "empty_slot_threshold": 1200,
    "item_match_threshold": 2600,
    "scan_interval_seconds": 2,
    "discord_webhook_env": "DISCORD_WEBHOOK_URL",
    "discord_max_items_in_message": 25,
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
    },
}


# ============================================================
# COMMON FUNCTIONS
# ============================================================
def ensure_directories():
    for path in (
        ASSETS_DIR,
        DIGITS_DIR,
        PROFILES_DIR,
        PROFILE_DIR,
        RESULTS_DIR,
        HISTORY_DIR,
        UNKNOWN_DIR,
        ITEM_IMAGES_DIR,
    ):
        os.makedirs(path, exist_ok=True)


def absolute_path(path):
    if not path:
        return None
    if os.path.isabs(path):
        return path

    normalized = path.replace("\\", "/").lstrip("./")
    profile_scoped = (
        normalized == "items"
        or normalized.startswith("items/")
        or normalized == "unknown_items"
        or normalized.startswith("unknown_items/")
    )

    if profile_scoped:
        return os.path.join(PROFILE_DIR, normalized)
    return os.path.join(BASE_DIR, path)


def sanitize_filename(name):
    value = unicodedata.normalize("NFKC", str(name)).strip()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    if not value:
        value = "item"

    reserved = {"CON", "PRN", "AUX", "NUL"}
    reserved |= {f"COM{i}" for i in range(1, 10)}
    reserved |= {f"LPT{i}" for i in range(1, 10)}

    if value.upper() in reserved:
        value = f"_{value}"

    return value[:120]


def next_image_path(item_name, extension=".png"):
    safe = sanitize_filename(item_name)
    index = 1
    while True:
        rel = f"items/{safe}_{index}{extension}"
        if not os.path.exists(absolute_path(rel)):
            return rel
        index += 1


def copy_image_as_png(source_path, relative_target):
    image = cv2.imread(source_path, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Cannot open image: {source_path}")

    target = absolute_path(relative_target)
    os.makedirs(os.path.dirname(target), exist_ok=True)

    if not cv2.imwrite(target, image):
        raise OSError(f"Cannot save image: {target}")

    return target


def normalize_items_db(data):
    if not isinstance(data, dict):
        data = {"items": []}

    raw_items = data.get("items", [])
    if not isinstance(raw_items, list):
        raw_items = []

    normalized = []

    for raw in raw_items:
        if not isinstance(raw, dict):
            continue

        name = str(raw.get("name", "")).strip()
        if not name:
            continue

        images = raw.get("images")
        if isinstance(images, str):
            images = [images]
        if not isinstance(images, list):
            images = []

        old_image = raw.get("image")
        if old_image and old_image not in images:
            images.insert(0, old_image)

        images = [str(x) for x in images if x]

        item = dict(raw)
        item["id"] = str(raw.get("id") or uuid.uuid4())
        item["name"] = name
        item["images"] = images
        item["image"] = images[0] if images else raw.get("image", "")

        try:
            item["stack_size"] = max(1, int(raw.get("stack_size", 64)))
        except (TypeError, ValueError):
            item["stack_size"] = 64

        aliases = raw.get("aliases", [])
        item["aliases"] = aliases if isinstance(aliases, list) else []
        item["enabled"] = bool(raw.get("enabled", True))

        normalized.append(item)

    return {"version": 2, "items": normalized}


def load_json(path, default_data):
    if not os.path.exists(path):
        return default_data.copy() if isinstance(default_data, dict) else default_data

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as error:
        print(f"[JSON] Failed to read {path}: {error}")
        return default_data.copy() if isinstance(default_data, dict) else default_data


def save_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_profiles():
    data = load_json(PROFILES_FILE, {})

    if (
        not isinstance(data, dict)
        or not isinstance(data.get("profiles"), list)
        or not data.get("profiles")
    ):
        data = {
            "version": 1,
            "active": "Default",
            "profiles": [{"name": "Default", "directory": "."}],
            "setup_complete": False,
        }
        save_json(PROFILES_FILE, data)

    return data


def save_profiles(data):
    save_json(PROFILES_FILE, data)


def profile_entry(name, directory=None):
    data = load_profiles()

    for entry in data.get("profiles", []):
        if entry.get("name") == name:
            return entry

    if directory is None:
        directory = os.path.join("profiles", sanitize_filename(name))

    entry = {"name": name, "directory": directory}
    data.setdefault("profiles", []).append(entry)
    save_profiles(data)
    return entry


def activate_profile(name):
    global PROFILE_DIR, CONFIG_FILE, ITEMS_FILE, RESULTS_DIR, UNKNOWN_DIR
    global ITEM_IMAGES_DIR, RESULTS_FILE, HISTORY_DIR
    global DISCORD_MESSAGE_IDS_FILE, DISCORD_WEBHOOK_FILE

    data = load_profiles()
    entry = profile_entry(name)
    directory = entry.get("directory", ".")

    if directory == ".":
        PROFILE_DIR = BASE_DIR
    else:
        PROFILE_DIR = os.path.join(BASE_DIR, directory)

    os.makedirs(PROFILE_DIR, exist_ok=True)

    CONFIG_FILE = os.path.join(PROFILE_DIR, "config.json")
    ITEMS_FILE = os.path.join(PROFILE_DIR, "items.json")
    RESULTS_DIR = os.path.join(PROFILE_DIR, "results")
    UNKNOWN_DIR = os.path.join(PROFILE_DIR, "unknown_items")
    ITEM_IMAGES_DIR = os.path.join(PROFILE_DIR, "items")
    RESULTS_FILE = os.path.join(RESULTS_DIR, "chests.json")
    HISTORY_DIR = os.path.join(RESULTS_DIR, "history")
    DISCORD_MESSAGE_IDS_FILE = os.path.join(RESULTS_DIR, "discord_message_ids.json")
    DISCORD_WEBHOOK_FILE = os.path.join(PROFILE_DIR, "discord_webhook.json")

    data["active"] = name
    save_profiles(data)
    ensure_directories()
    return entry


def deep_merge(default_data, user_data):
    result = default_data.copy()

    for key, value in user_data.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value

    return result


def screenshot_bgr():
    screenshot = pyautogui.screenshot()
    return cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)


def resize_image(image, width, height):
    if image is None or image.size == 0:
        return None

    return cv2.resize(
        image,
        (max(1, int(width)), max(1, int(height))),
        interpolation=cv2.INTER_AREA,
    )


def crop_relative(image, rect):
    if image is None or image.size == 0:
        return None

    ih, iw = image.shape[:2]
    x1 = max(0, int(rect[0] * iw))
    y1 = max(0, int(rect[1] * ih))
    w = max(1, int(rect[2] * iw))
    h = max(1, int(rect[3] * ih))
    x2 = min(iw, x1 + w)
    y2 = min(ih, y1 + h)

    return image[y1:y2, x1:x2]


def image_distance(image_a, image_b):
    if image_a is None or image_b is None:
        return float("inf")
    if image_a.size == 0 or image_b.size == 0:
        return float("inf")

    image_a = resize_image(image_a, 48, 48)
    image_b = resize_image(image_b, 48, 48)

    gray_a = cv2.cvtColor(image_a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(image_b, cv2.COLOR_BGR2GRAY)
    mse = np.mean((gray_a.astype(np.float32) - gray_b.astype(np.float32)) ** 2)

    hsv_a = cv2.cvtColor(image_a, cv2.COLOR_BGR2HSV)
    hsv_b = cv2.cvtColor(image_b, cv2.COLOR_BGR2HSV)

    hist_a = cv2.calcHist([hsv_a], [0, 1], None, [16, 16], [0, 180, 0, 256])
    hist_b = cv2.calcHist([hsv_b], [0, 1], None, [16, 16], [0, 180, 0, 256])

    cv2.normalize(hist_a, hist_a)
    cv2.normalize(hist_b, hist_b)

    correlation = cv2.compareHist(hist_a, hist_b, cv2.HISTCMP_CORREL)
    color_penalty = (1.0 - max(-1.0, min(1.0, correlation))) * 1000

    return float(mse + color_penalty)


def split_discord_message(text, limit=1800):
    if not text:
        return []

    fence_pattern = re.compile(r"(?ms)^```[^\n]*\n.*?^```(?=\n|$)")

    def split_plain_block(block, max_len):
        if not block:
            return []

        parts = []
        current = ""

        for line in block.splitlines():
            line = line.rstrip()

            if len(line) > max_len:
                if current:
                    parts.append(current.rstrip())
                    current = ""

                for pos in range(0, len(line), max_len):
                    parts.append(line[pos:pos + max_len])

                continue

            candidate = line if not current else current + "\n" + line

            if len(candidate) <= max_len:
                current = candidate
            else:
                if current:
                    parts.append(current.rstrip())
                current = line

        if current.strip():
            parts.append(current.rstrip())

        return parts

    def split_code_block(block, max_len):
        lines = block.splitlines()

        if len(lines) < 2:
            return [block] if len(block) <= max_len else []

        opening = lines[0]
        closing = lines[-1]
        body = lines[1:-1]

        header_lines = []
        data_start = 0

        if body:
            header_lines.append(body[0])
            data_start = 1

        if len(body) > 1 and body[1].startswith("+-") and body[1].endswith("-+"):
            header_lines.append(body[1])
            data_start = 2

        reserved = len(opening) + 1 + len(closing) + 2
        body_limit = max(100, max_len - reserved)

        chunks = []
        current = list(header_lines)
        current_length = sum(len(line) for line in current) + max(0, len(current) - 1)

        def flush():
            if current:
                chunks.append(opening + "\n" + "\n".join(current) + "\n" + closing)

        for line in body[data_start:]:
            line = line.rstrip()

            if len(line) > body_limit:
                if current:
                    flush()
                    current[:] = header_lines
                    current_length = sum(len(x) for x in current) + max(0, len(current) - 1)

                chunks.append(opening + "\n" + line + "\n" + closing)
                continue

            extra = len(line) + (1 if current else 0)

            if current and current_length + extra > body_limit:
                flush()
                current[:] = header_lines
                current_length = sum(len(x) for x in current) + max(0, len(current) - 1)

            current.append(line)
            current_length += len(line) + (1 if len(current) > 1 else 0)

        if current:
            flush()

        return chunks

    parts = []
    position = 0

    for match in fence_pattern.finditer(text):
        before = text[position:match.start()]
        parts.extend(split_plain_block(before, limit))
        parts.extend(split_code_block(match.group(0), limit))
        position = match.end()

    parts.extend(split_plain_block(text[position:], limit))

    return [part for part in parts if part and part.strip()]


# ============================================================
# CHEST READER
# ============================================================
class ChestReader:
    def __init__(self):
        profiles = load_profiles()
        activate_profile(profiles.get("active", "Default"))
        ensure_directories()

        self.config = deep_merge(DEFAULT_CONFIG, load_json(CONFIG_FILE, {}))
        save_json(CONFIG_FILE, self.config)

        self.items_db = normalize_items_db(load_json(ITEMS_FILE, {"items": []}))
        save_json(ITEMS_FILE, self.items_db)

        self.ref_w, self.ref_h = self.config["reference_screen_size"]
        self.grid = self.config["chest_grid"]
        self.chest_template = self.read_image(self.config["chest_template"])
        self.empty_slot_template = self.read_image(self.config["empty_slot_template"])
        self.digit_templates = self.load_digit_templates()

        self.last_chest_hash = None
        self.chest_was_open = False
        self.prompted_unknowns = set()
        self.unknown_item_prompt = None

        self._stack_size_cache = {}
        self._rebuild_stack_size_cache()

        self.print_startup_status()

    def _rebuild_stack_size_cache(self):
        self._stack_size_cache = {}
        for item in self.items_db.get("items", []):
            name = item.get("name")
            if name:
                self._stack_size_cache[name] = item.get("stack_size", 64)

    def get_item_stack_size(self, item_name, default_stack_size=64):
        return self._stack_size_cache.get(item_name, default_stack_size)

    def play_scan_start_sound(self):
        if not WINSOUND_AVAILABLE:
            return
        try:
            winsound.Beep(880, 120)
        except Exception:
            pass

    def play_success_sound(self):
        if not WINSOUND_AVAILABLE:
            return
        try:
            winsound.Beep(880, 120)
            winsound.Beep(1175, 180)
        except Exception:
            pass

    def print_startup_status(self):
        print("\n===================================================")
        print("                 CHEST SCANNER")
        print("===================================================")
        print(f"Folder: {BASE_DIR}")
        print(f"Reference resolution: {self.ref_w} x {self.ref_h}")
        print(f"Chest slots: {self.grid['columns']} x {self.grid['rows']}")
        print(f"Items in database: {len(self.items_db['items'])}")
        print(f"Digit templates loaded: {len(self.digit_templates)} / 10")
        print("===================================================\n")

    def read_image(self, path):
        full_path = absolute_path(path)
        if not full_path or not os.path.exists(full_path):
            return None
        return cv2.imread(full_path, cv2.IMREAD_COLOR)

    def load_digit_templates(self):
        templates = {}

        for digit in range(10):
            path = os.path.join(DIGITS_DIR, f"{digit}.png")
            if not os.path.exists(path):
                continue

            image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if image is None:
                continue

            prepared = self.prepare_digit(image)
            if prepared is not None:
                templates[str(digit)] = prepared

        return templates

    @staticmethod
    def make_digit_mask(image):
        if image is None or image.size == 0:
            return None

        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image.copy()

        if len(np.unique(gray)) <= 3:
            return cv2.inRange(gray, 100, 255)

        mask = cv2.inRange(gray, 250, 255)
        kernel = np.ones((2, 2), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
        return mask

    @staticmethod
    def prepare_digit(image):
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

        scale = min(
            (canvas_width - padding * 2) / max(1, width),
            (canvas_height - padding * 2) / max(1, height),
        )

        new_width = max(1, round(width * scale))
        new_height = max(1, round(height * scale))

        digit = cv2.resize(
            digit,
            (new_width, new_height),
            interpolation=cv2.INTER_NEAREST,
        )

        canvas = np.zeros((canvas_height, canvas_width), dtype=np.uint8)
        x_offset = (canvas_width - new_width) // 2
        y_offset = (canvas_height - new_height) // 2

        canvas[y_offset:y_offset + new_height, x_offset:x_offset + new_width] = digit
        return canvas

    def match_digit(self, digit_image):
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

            for shift_y in range(-3, 4):
                for shift_x in range(-3, 4):
                    transform = np.float32([[1, 0, shift_x], [0, 1, shift_y]])

                    shifted = cv2.warpAffine(
                        template_mask.astype(np.uint8),
                        transform,
                        (template_mask.shape[1], template_mask.shape[0]),
                        flags=cv2.INTER_NEAREST,
                        borderValue=0,
                    ).astype(bool)

                    intersection = np.logical_and(prepared_mask, shifted).sum()
                    union = np.logical_or(prepared_mask, shifted).sum()

                    if union == 0:
                        continue

                    iou = intersection / union
                    pixels_a = prepared_mask.sum()
                    pixels_b = shifted.sum()

                    size_score = 1.0 - (abs(pixels_a - pixels_b) / max(1, pixels_a, pixels_b))
                    score = iou * 0.80 + size_score * 0.20

                    if score > best_score:
                        best_score = score
                        best_digit = digit

        if best_score < 0.10:
            return None, best_score

        return best_digit, best_score

    def read_quantity(self, slot_image, debug_name=None):
        if not self.digit_templates:
            return 1

        count_image = crop_relative(slot_image, self.config["count_roi_in_slot"])
        if count_image is None or count_image.size == 0:
            return 1

        binary = self.make_digit_mask(count_image)
        if binary is None:
            return 1

        ih, iw = binary.shape[:2]
        labels_count, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)

        characters = []

        for label in range(1, labels_count):
            x, y, width, height, area = stats[label]

            if area < 2 or height < 3:
                continue
            if width > iw * 0.80 or height > ih * 0.95:
                continue

            characters.append({
                "x1": x,
                "y1": y,
                "x2": x + width,
                "y2": y + height,
            })

        if not characters:
            return 1

        characters.sort(key=lambda item: item["x1"])

        result = []

        for character in characters:
            padding = 1

            x1 = max(0, character["x1"] - padding)
            y1 = max(0, character["y1"] - padding)
            x2 = min(iw, character["x2"] + padding)
            y2 = min(ih, character["y2"] + padding)

            digit_image = binary[y1:y2, x1:x2]
            digit, _ = self.match_digit(digit_image)

            if digit is not None:
                result.append(digit)

        number_text = "".join(result)
        if not number_text:
            return 1

        try:
            quantity = int(number_text)
        except ValueError:
            return 1

        if quantity < 1 or quantity > 9999:
            return 1

        return quantity

    def chest_is_open(self, screen):
        if self.chest_template is None:
            return False

        screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
        template_gray = cv2.cvtColor(self.chest_template, cv2.COLOR_BGR2GRAY)

        th, tw = template_gray.shape[:2]
        sh, sw = screen_gray.shape[:2]

        if th > sh or tw > sw:
            return False

        result = cv2.matchTemplate(screen_gray, template_gray, cv2.TM_CCOEFF_NORMED)
        _, maximum, _, _ = cv2.minMaxLoc(result)

        return maximum >= self.config["chest_template_threshold"]

    def get_grid_rect(self, screen):
        sh, sw = screen.shape[:2]
        scale_x = sw / self.ref_w
        scale_y = sh / self.ref_h

        return {
            "x": round(self.grid["x"] * scale_x),
            "y": round(self.grid["y"] * scale_y),
            "width": round(self.grid["width"] * scale_x),
            "height": round(self.grid["height"] * scale_y),
            "columns": self.grid["columns"],
            "rows": self.grid["rows"],
        }

    def get_slots(self, screen):
        grid = self.get_grid_rect(screen)

        slot_width = grid["width"] / grid["columns"]
        slot_height = grid["height"] / grid["rows"]

        slots = []

        for row in range(grid["rows"]):
            for column in range(grid["columns"]):
                x1 = round(grid["x"] + column * slot_width) + 1
                x2 = round(grid["x"] + (column + 1) * slot_width) - 1
                y1 = round(grid["y"] + row * slot_height) + 1
                y2 = round(grid["y"] + (row + 1) * slot_height) - 1

                slots.append({
                    "row": row,
                    "column": column,
                    "image": screen[y1:y2, x1:x2],
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2,
                })

        return slots

    def show_grid_debug(self):
        screen = screenshot_bgr()
        grid = self.get_grid_rect(screen)
        preview = screen.copy()

        cv2.rectangle(
            preview,
            (grid["x"], grid["y"]),
            (grid["x"] + grid["width"], grid["y"] + grid["height"]),
            (0, 0, 255),
            2,
        )

        for index, slot in enumerate(self.get_slots(screen), start=1):
            cv2.rectangle(
                preview,
                (slot["x1"] - 1, slot["y1"] - 1),
                (slot["x2"] + 1, slot["y2"] + 1),
                (0, 255, 0),
                1,
            )
            cv2.putText(
                preview,
                str(index),
                (slot["x1"] + 3, slot["y1"] + 13),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.35,
                (0, 0, 255),
                1,
                cv2.LINE_AA,
            )

        cv2.putText(
            preview,
            "RED = grid | GREEN = slots | Press any key to close",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )

        cv2.imshow("Chest grid calibration", preview)
        cv2.waitKey(0)
        cv2.destroyAllWindows()

    def save_quantity_debug(self):
        screen = screenshot_bgr()
        slots = self.get_slots(screen)

        debug_dir = os.path.join(RESULTS_DIR, "quantity_debug")
        os.makedirs(debug_dir, exist_ok=True)

        for slot in slots:
            slot_image = slot["image"]
            if slot_image is None or slot_image.size == 0:
                continue

            count_image = crop_relative(slot_image, self.config["count_roi_in_slot"])
            if count_image is None or count_image.size == 0:
                continue

            binary = self.make_digit_mask(count_image)
            row = slot["row"] + 1
            column = slot["column"] + 1

            for suffix, img in (("original", count_image), ("binary", binary)):
                large = cv2.resize(img, None, fx=15, fy=15, interpolation=cv2.INTER_NEAREST)
                cv2.imwrite(os.path.join(debug_dir, f"slot_r{row}_c{column}_{suffix}.png"), large)

        print(f"[DEBUG] Quantity debug saved: {debug_dir}")

    def is_empty_slot(self, slot_image):
        if self.empty_slot_template is None:
            return False
        if slot_image is None or slot_image.size == 0:
            return True

        template = resize_image(self.empty_slot_template, slot_image.shape[1], slot_image.shape[0])
        distance = image_distance(slot_image, template)

        return distance < self.config["empty_slot_threshold"]

    def extract_icon(self, slot_image):
        return crop_relative(slot_image, self.config["icon_roi_in_slot"])

    def detect_item(self, icon_image):
        candidates = []

        for item in self.items_db["items"]:
            if not item.get("enabled", True):
                continue

            item_name = item.get("name")
            if not item_name:
                continue

            image_paths = item.get("images") or [item.get("image")]

            for image_path in image_paths:
                if not image_path:
                    continue

                template = self.read_image(image_path)
                if template is None:
                    continue

                template = resize_image(template, icon_image.shape[1], icon_image.shape[0])
                distance = image_distance(icon_image, template)

                candidates.append({
                    "name": item_name,
                    "image": image_path,
                    "distance": distance,
                })

        if not candidates:
            unknown_name = self.add_unknown_item(icon_image)
            return unknown_name, True, float("inf")

        best_per_item = {}

        for candidate in candidates:
            name = candidate["name"]
            if name not in best_per_item or candidate["distance"] < best_per_item[name]["distance"]:
                best_per_item[name] = candidate

        sorted_candidates = sorted(best_per_item.values(), key=lambda x: x["distance"])
        best = sorted_candidates[0]

        best_name = best["name"]
        best_distance = best["distance"]
        second_distance = sorted_candidates[1]["distance"] if len(sorted_candidates) > 1 else float("inf")

        threshold = self.config.get("item_match_threshold", 2600)
        margin = self.config.get("item_match_margin", 150)
        difference = second_distance - best_distance

        if self.config.get("print_item_candidates", False):
            print(
                f"[ITEM] Best: {best_name} | distance={best_distance:.1f} "
                f"| second={second_distance:.1f} | diff={difference:.1f}"
            )

        if best_distance > threshold:
            unknown_name = self.add_unknown_item(icon_image)
            return unknown_name, True, best_distance

        if second_distance != float("inf") and difference < margin:
            unknown_name = self.add_unknown_item(icon_image)
            return unknown_name, True, best_distance

        if best_name.startswith("unknown_"):
            renamed_name = self.add_unknown_item(icon_image)
            return renamed_name, renamed_name.startswith("unknown_"), best_distance

        return best_name, False, best_distance

    def add_unknown_item(self, icon_image):
        if icon_image is None or icon_image.size == 0:
            return "unknown_empty"

        small_image = resize_image(icon_image, 32, 32)
        item_hash = hashlib.md5(small_image.tobytes()).hexdigest()[:10]
        unknown_name = f"unknown_{item_hash}"
        relative_path = f"unknown_items/{unknown_name}.png"
        full_path = absolute_path(relative_path)

        existing_item = None

        for item in self.items_db.get("items", []):
            item_images = item.get("images") or [item.get("image")]
            normalized_images = [str(path) for path in item_images if path]

            if item.get("name") == unknown_name or relative_path in normalized_images:
                existing_item = item
                break

        if (
            existing_item is not None
            and existing_item.get("name", "").strip()
            and not existing_item.get("name", "").startswith("unknown_")
        ):
            return existing_item["name"]

        if not os.path.exists(full_path):
            cv2.imwrite(full_path, icon_image)

        if unknown_name in self.prompted_unknowns:
            return existing_item.get("name", unknown_name) if existing_item else unknown_name

        self.prompted_unknowns.add(unknown_name)

        try:
            if callable(self.unknown_item_prompt):
                user_name = (self.unknown_item_prompt(unknown_name, full_path) or "").strip()
            else:
                user_name = input(f"Name for {unknown_name}: ").strip()
        except (EOFError, KeyboardInterrupt):
            user_name = ""
        except Exception:
            user_name = ""

        final_name = user_name or unknown_name

        if existing_item is None:
            existing_item = {
                "id": str(uuid.uuid4()),
                "name": final_name,
                "stack_size": 64,
                "images": [relative_path],
                "image": relative_path,
                "aliases": [],
                "enabled": True,
            }
            self.items_db.setdefault("items", []).append(existing_item)
        else:
            existing_item["name"] = final_name
            images = existing_item.get("images") or [existing_item.get("image")]
            images = [str(x) for x in images if x]

            if relative_path not in images:
                images.append(relative_path)

            existing_item["images"] = images
            existing_item["image"] = images[0] if images else relative_path

        if final_name != unknown_name:
            new_relative = next_image_path(final_name, ".png")
            new_full = absolute_path(new_relative)

            try:
                if os.path.abspath(full_path) != os.path.abspath(new_full):
                    shutil.move(full_path, new_full)

                    images = existing_item.get("images", [])
                    images = [new_relative if path == relative_path else path for path in images]

                    existing_item["images"] = images
                    existing_item["image"] = images[0] if images else new_relative
            except OSError:
                pass

        self.items_db = normalize_items_db(self.items_db)
        save_json(ITEMS_FILE, self.items_db)
        self._rebuild_stack_size_cache()

        return final_name

    def reload_profile(self):
        ensure_directories()

        self.config = deep_merge(DEFAULT_CONFIG, load_json(CONFIG_FILE, {}))
        save_json(CONFIG_FILE, self.config)

        self.items_db = normalize_items_db(load_json(ITEMS_FILE, {"items": []}))
        save_json(ITEMS_FILE, self.items_db)

        self.ref_w, self.ref_h = self.config["reference_screen_size"]
        self.grid = self.config["chest_grid"]
        self.chest_template = self.read_image(self.config["chest_template"])
        self.empty_slot_template = self.read_image(self.config["empty_slot_template"])
        self.digit_templates = self.load_digit_templates()

        self.last_chest_hash = None
        self.chest_was_open = False
        self.prompted_unknowns.clear()

        self._rebuild_stack_size_cache()

    def save_chest_result(self, chest_data):
        data = load_json(RESULTS_FILE, {"chests": []})
        if not isinstance(data, dict):
            data = {"chests": []}

        data.setdefault("chests", []).append(chest_data)
        save_json(RESULTS_FILE, data)

    def scan_chest(self, force=False):
        self.play_scan_start_sound()
        time.sleep(0.25)

        screen = screenshot_bgr()

        if not self.chest_is_open(screen):
            if self.chest_was_open:
                self.chest_was_open = False
                self.last_chest_hash = None
            return None

        self.chest_was_open = True
        grid = self.get_grid_rect(screen)

        chest_image = screen[
            grid["y"]:grid["y"] + grid["height"],
            grid["x"]:grid["x"] + grid["width"]
        ]

        small_chest = resize_image(chest_image, 100, 60)
        chest_hash = hashlib.md5(small_chest.tobytes()).hexdigest()

        if not force and chest_hash == self.last_chest_hash:
            return None

        self.last_chest_hash = chest_hash

        slot_list = []
        totals = {}
        unknown_items = set()

        for slot in self.get_slots(screen):
            slot_image = slot["image"]

            if slot_image is None or slot_image.size == 0:
                continue

            if self.is_empty_slot(slot_image):
                continue

            icon_image = self.extract_icon(slot_image)
            if icon_image is None or icon_image.size == 0:
                continue

            item_name, is_unknown, similarity = self.detect_item(icon_image)
            quantity = self.read_quantity(
                slot_image,
                debug_name=f"r{slot['row'] + 1}_c{slot['column'] + 1}",
            )

            if is_unknown:
                unknown_items.add(item_name)

            slot_list.append({
                "row": slot["row"] + 1,
                "column": slot["column"] + 1,
                "item": item_name,
                "quantity": quantity,
                "unknown": is_unknown,
                "similarity_score": round(float(similarity), 2),
            })

            totals[item_name] = totals.get(item_name, 0) + quantity

        chest_data = {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M:%S"),
            "screen_resolution": [screen.shape[1], screen.shape[0]],
            "occupied_slots": len(slot_list),
            "unique_items": len(totals),
            "unknown_items": sorted(list(unknown_items)),
            "slots": slot_list,
            "totals": totals,
        }

        self.save_chest_result(chest_data)
        self.play_success_sound()

        return chest_data

    @staticmethod
    def format_number(number):
        return f"{number:,}".replace(",", " ")

    def build_discord_message(self, chests):
        if not chests:
            return "**Chest database is empty**\nOpen a chest and press `F8` first."

        discord_config = self.config.get("discord_message", {})
        global_totals = {}
        all_unknown_items = set()

        for chest in chests:
            for item_name, quantity in chest.get("totals", {}).items():
                global_totals[item_name] = global_totals.get(item_name, 0) + quantity

            for unknown_item in chest.get("unknown_items", []):
                all_unknown_items.add(unknown_item)

        last_timestamp = chests[-1].get("timestamp", "unknown")
        default_stack_size = discord_config.get("stack_size", 64)

        table_config = discord_config.get("table", {})
        name_width = max(12, int(table_config.get("name_width", 30)))
        total_width = max(8, int(table_config.get("total_width", 12)))
        stacks_width = max(6, int(table_config.get("stacks_width", 10)))

        def display_width(value):
            width = 0

            for char in str(value):
                if unicodedata.combining(char):
                    continue

                east_width = unicodedata.east_asian_width(char)
                width += 2 if east_width in ("W", "F") else 1

            return width

        def truncate_display(value, width):
            text = str(value)

            if display_width(text) <= width:
                return text

            result = []
            current_width = 0
            target = max(1, width - 1)

            for char in text:
                char_width = display_width(char)

                if current_width + char_width > target:
                    break

                result.append(char)
                current_width += char_width

            return "".join(result) + "…"

        def fit(text, width, align="left"):
            text = truncate_display(text, width)
            padding = max(0, width - display_width(text))

            if align == "right":
                return " " * padding + text

            return text + " " * padding

        def table_line(name, total, stacks):
            return (
                f"| {fit(name, name_width)} | "
                f"{fit(total, total_width, 'right')} | "
                f"{fit(stacks, stacks_width, 'right')} |"
            )

        separator = (
            f"+-{'-' * name_width}-+-"
            f"{'-' * total_width}-+-"
            f"{'-' * stacks_width}-+"
        )

        columns = table_line("Название", "Всего", "Стаков")
        item_lines = []

        for item_name, quantity in sorted(global_totals.items(), key=lambda x: x[0].lower()):
            item_stack_size = self.get_item_stack_size(item_name, default_stack_size)

            if item_stack_size and quantity < item_stack_size:
                stacks_text = ""
            else:
                stacks = int(quantity) // int(item_stack_size) if item_stack_size else int(quantity)
                stacks_text = str(stacks)

            item_lines.append(table_line(item_name, self.format_number(quantity), stacks_text))

        items_text = "```text\n" + "\n".join([columns, separator] + item_lines) + "\n```"

        values = {
            "chests": len(chests),
            "last_scan": last_timestamp,
            "items": items_text,
            "unknowns": "\n".join(sorted(all_unknown_items)),
        }

        header = discord_config.get("header", "")
        scanned_chests = discord_config.get(
            "scanned_chests",
            "Отсканировано сундуков: {chests}"
        ).format(**values)

        last_scan = discord_config.get(
            "last_scan",
            "Время последнего сканирования: {last_scan}"
        ).format(**values)

        items_title = discord_config.get("items_title", "══════════Предметы══════════")

        lines = []

        if header:
            lines.extend([header, ""])

        lines.extend([scanned_chests, last_scan, "", items_title, "", items_text])

        if all_unknown_items:
            unknown_title = discord_config.get(
                "unknown_title",
                "══════════Неизвестные предметы══════════",
            )
            unknown_template = discord_config.get("unknown_item", "• {name}")

            unknowns_text = discord_config.get("item_separator", "\n").join(
                unknown_template.format(name=name) for name in sorted(all_unknown_items)
            )

            lines.extend(["", unknown_title, "", unknowns_text])

        return "\n".join(lines)

    def get_discord_webhook_url(self):
        env_name = self.config.get("discord_webhook_env", "DISCORD_WEBHOOK_URL")

        if os.path.exists(DISCORD_WEBHOOK_FILE):
            try:
                data = load_json(DISCORD_WEBHOOK_FILE, {})

                if isinstance(data, dict):
                    webhook_url = (
                        data.get("webhook_url")
                        or data.get(env_name)
                        or data.get("DISCORD_WEBHOOK_URL")
                    )

                    if isinstance(webhook_url, str) and webhook_url.strip():
                        return webhook_url.strip()
            except Exception:
                pass

        webhook_url = os.getenv(env_name, "").strip()
        return webhook_url or None

    @staticmethod
    def load_discord_message_ids():
        data = load_json(DISCORD_MESSAGE_IDS_FILE, {"message_ids": []})
        ids = data.get("message_ids", []) if isinstance(data, dict) else []
        return [str(mid) for mid in ids if mid]

    @staticmethod
    def save_discord_message_ids(message_ids):
        save_json(
            DISCORD_MESSAGE_IDS_FILE,
            {"message_ids": [str(mid) for mid in message_ids]},
        )

    @staticmethod
    def _discord_response_error(response):
        try:
            body = response.json()
            if isinstance(body, dict) and body.get("message"):
                return str(body["message"])
        except ValueError:
            pass

        return response.text[:500]

    def _create_discord_message(self, webhook_url, content):
        try:
            payload = {
                "content": content,
                "username": "Chest Scanner",
            }

            response = requests.post(
                webhook_url,
                params={"wait": "true"},
                json=payload,
                timeout=30,
            )

            if response.status_code != 200:
                return None, f"Discord HTTP {response.status_code}: {self._discord_response_error(response)}"

            try:
                message_id = response.json().get("id")
            except ValueError:
                message_id = None

            if not message_id:
                return None, "Discord did not return created message ID."

            return str(message_id), None

        except requests.RequestException as error:
            return None, f"Network error: {error}"

    def _edit_discord_message(self, webhook_url, message_id, content):
        try:
            payload = {
                "content": content,
                "username": "Chest Scanner",
            }

            response = requests.patch(
                f"{webhook_url}/messages/{message_id}",
                json=payload,
                timeout=30,
            )

            if response.status_code == 404:
                return False, "not_found"

            if response.status_code != 200:
                return False, f"Discord HTTP {response.status_code}: {self._discord_response_error(response)}"

            return True, None

        except requests.RequestException as error:
            return False, f"Network error: {error}"

    def _delete_discord_message(self, webhook_url, message_id):
        try:
            response = requests.delete(
                f"{webhook_url}/messages/{message_id}",
                timeout=15,
            )

            if response.status_code in (200, 204, 404):
                return True, None

            return False, f"Discord HTTP {response.status_code}: {self._discord_response_error(response)}"

        except requests.RequestException as error:
            return False, f"Network error: {error}"

    def archive_report_after_discord(self):
        data = load_json(RESULTS_FILE, {"chests": []})
        chests = data.get("chests", []) if isinstance(data, dict) else []

        if not chests:
            return None

        timestamp = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%Y-%m-%d_%H-%M-%S")
        history_path = os.path.join(HISTORY_DIR, f"report_{timestamp}.json")

        counter = 2
        while os.path.exists(history_path):
            history_path = os.path.join(HISTORY_DIR, f"report_{timestamp}_{counter}.json")
            counter += 1

        save_json(history_path, data)
        save_json(RESULTS_FILE, {"chests": []})

        return history_path

    def send_all_chests_to_discord(self):
        webhook_url = self.get_discord_webhook_url()

        if not webhook_url:
            return False, "Discord Webhook not found."

        if not webhook_url.startswith((
            "https://discord.com/api/webhooks/",
            "https://discordapp.com/api/webhooks/",
        )):
            return False, "Invalid Discord Webhook URL."

        if not os.path.exists(RESULTS_FILE):
            return False, "chests.json not found."

        data = load_json(RESULTS_FILE, {"chests": []})
        chests = data.get("chests", []) if isinstance(data, dict) else []

        if not chests:
            return False, "No saved scans."

        full_message = self.build_discord_message(chests)
        message_parts = split_discord_message(full_message, limit=1800) or ["Report is empty."]

        total_parts = len(message_parts)
        contents = []

        for part in message_parts:
            if len(part) > 1900:
                return False, "Internal error: report part exceeds Discord limit."
            contents.append(part)

        old_ids = self.load_discord_message_ids()
        new_ids = []

        edited = 0
        created = 0
        deleted = 0

        try:
            for index, content in enumerate(contents):
                if index < len(old_ids):
                    message_id = old_ids[index]
                    ok, error = self._edit_discord_message(webhook_url, message_id, content)

                    if ok:
                        new_ids.append(message_id)
                        edited += 1
                    elif error == "not_found":
                        created_id, create_error = self._create_discord_message(webhook_url, content)

                        if not created_id:
                            return False, f"Cannot restore message: {create_error}"

                        new_ids.append(created_id)
                        created += 1
                    else:
                        return False, f"Cannot edit message {message_id}: {error}"
                else:
                    message_id, error = self._create_discord_message(webhook_url, content)

                    if not message_id:
                        return False, f"Cannot create message: {error}"

                    new_ids.append(message_id)
                    created += 1

                time.sleep(0.5)

            for message_id in old_ids[total_parts:]:
                ok, error = self._delete_discord_message(webhook_url, message_id)

                if ok:
                    deleted += 1
                else:
                    return False, f"Cannot delete extra message {message_id}: {error}"

                time.sleep(0.3)

            self.save_discord_message_ids(new_ids)
            history_path = self.archive_report_after_discord()

            if history_path:
                history_name = os.path.basename(history_path)
                return True, (
                    f"Discord updated: edited {edited}, created {created}, "
                    f"deleted extra {deleted}. Report archived as {history_name}. "
                    "Current chests.json cleared."
                )

            return True, (
                f"Discord updated: edited {edited}, created {created}, "
                f"deleted extra {deleted}."
            )

        except requests.RequestException as error:
            return False, f"Network error: {error}"
        except Exception as error:
            return False, f"Unexpected error: {error}"


# ============================================================
# GUI
# ============================================================
class ScannerGUI:
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
        self.root.geometry("1100x760")
        self.root.minsize(920, 650)
        self.root.configure(bg=self.BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.log_queue = Queue()
        self.busy = False
        self.running = True

        self.reader.unknown_item_prompt = self._prompt_unknown_item

        self._setup_style()
        self._build_ui()
        self._refresh_profile_label()
        self._refresh_stats()
        self._poll_logs()
        self._keyboard_loop()

        profiles = load_profiles()
        if not profiles.get("setup_complete", False):
            self.root.after(100, self._safe_first_run_wizard)

    def _safe_first_run_wizard(self):
        try:
            self._first_run_wizard()
        except Exception as error:
            try:
                self.root.deiconify()
                self.root.lift()
                self.root.focus_force()
            except Exception:
                pass

            try:
                messagebox.showerror("First run wizard error", str(error))
            except Exception:
                pass

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
            padx=16,
            pady=10,
            cursor="hand2",
            font=("Segoe UI", 10, "bold"),
        )

        button.bind("<Enter>", lambda e: button.configure(bg=active))
        button.bind("<Leave>", lambda e: button.configure(bg=bg))

        return button

    def _card(self, parent, title, value, color=None):
        frame = tk.Frame(parent, bg=self.PANEL, highlightbackground=self.BORDER, highlightthickness=1)

        tk.Label(
            frame,
            text=title.upper(),
            bg=self.PANEL,
            fg=self.MUTED,
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", padx=16, pady=(12, 2))

        label = tk.Label(
            frame,
            text=value,
            bg=self.PANEL,
            fg=color or self.TEXT,
            font=("Segoe UI", 21, "bold"),
        )

        label.pack(anchor="w", padx=16, pady=(0, 12))

        return frame, label

    def _build_ui(self):
        header = tk.Frame(self.root, bg=self.BG)
        header.pack(fill="x", padx=26, pady=(22, 12))

        left = tk.Frame(header, bg=self.BG)
        left.pack(side="left")

        tk.Label(
            left,
            text="Chest Scanner",
            bg=self.BG,
            fg=self.TEXT,
            font=("Segoe UI", 24, "bold"),
        ).pack(anchor="w")

        tk.Label(
            left,
            text="Minecraft chest recognition",
            bg=self.BG,
            fg=self.MUTED,
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(2, 0))

        self.profile_label = tk.Label(
            header,
            text="Profile: Default",
            bg=self.BG,
            fg=self.MUTED,
            font=("Segoe UI", 9, "bold"),
        )
        self.profile_label.pack(side="right", pady=11, padx=(0, 14))

        self.status_dot = tk.Label(
            header,
            text="●  READY",
            bg=self.BG,
            fg=self.GREEN,
            font=("Segoe UI", 10, "bold"),
        )
        self.status_dot.pack(side="right", pady=10)

        actions = tk.Frame(self.root, bg=self.BG)
        actions.pack(fill="x", padx=26, pady=(0, 16))

        self._button(actions, "Scan", lambda: self._run("Scan", self._scan), True).pack(side="left", padx=(0, 7))
        self._button(actions, "Discord", lambda: self._run("Discord send", self._discord)).pack(side="left", padx=4)
        self._button(actions, "Settings", self.open_settings).pack(side="left", padx=4)
        self._button(actions, "Items", self.open_item_database).pack(side="left", padx=4)
        self._button(actions, "Profiles", self.open_profile_manager).pack(side="left", padx=4)
        self._button(actions, "Grid F7", lambda: self._run("Grid debug", self.reader.show_grid_debug)).pack(side="left", padx=4)
        self._button(actions, "Digits F6", lambda: self._run("Digits debug", self.reader.save_quantity_debug)).pack(side="left", padx=4)
        self._button(actions, "Exit", self.close).pack(side="right")

        stats = tk.Frame(self.root, bg=self.BG)
        stats.pack(fill="x", padx=26, pady=(0, 16))

        for i in range(4):
            stats.grid_columnconfigure(i, weight=1)

        self.cards = {}

        for i, (key, title, value, color) in enumerate([
            ("chests", "Chests", "0", None),
            ("items", "Items", "0", None),
            ("unique", "Unique", "0", self.ACCENT_HOVER),
            ("unknown", "Unknown", "0", self.YELLOW),
        ]):
            card, label = self._card(stats, title, value, color)
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 6, 6 if i < 3 else 0))
            self.cards[key] = label

        content = tk.Frame(self.root, bg=self.BG)
        content.pack(fill="both", expand=True, padx=26, pady=(0, 16))

        log_panel = tk.Frame(content, bg=self.PANEL, highlightbackground=self.BORDER, highlightthickness=1)
        log_panel.pack(fill="both", expand=True)

        tk.Label(
            log_panel,
            text="LOG",
            bg=self.PANEL,
            fg=self.MUTED,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", padx=16, pady=(14, 8))

        self.log = tk.Text(
            log_panel,
            bg=self.PANEL,
            fg="#cbd2dc",
            insertbackground=self.TEXT,
            relief="flat",
            bd=0,
            wrap="word",
            font=("Consolas", 9),
            padx=16,
            pady=4,
        )
        self.log.pack(fill="both", expand=True, side="left")

        scroll = tk.Scrollbar(log_panel, command=self.log.yview)
        scroll.pack(side="right", fill="y", padx=(0, 7), pady=(0, 8))
        self.log.configure(yscrollcommand=scroll.set)

        footer = tk.Frame(self.root, bg=self.BG)
        footer.pack(fill="x", padx=26, pady=(0, 16))

        tk.Label(
            footer,
            text="F6 digits    F7 grid    F8 scan    F9 Discord    F10 exit",
            bg=self.BG,
            fg=self.MUTED,
            font=("Segoe UI", 9),
        ).pack(side="left")

        self.last_scan_label = tk.Label(
            footer,
            text="Last action: —",
            bg=self.BG,
            fg=self.MUTED,
            font=("Segoe UI", 9),
        )
        self.last_scan_label.pack(side="right")

        self._log("Interface started.")

        if KEYBOARD_AVAILABLE:
            self._log("Hotkeys F6-F10 enabled.")
        else:
            self._log("Hotkeys unavailable.", self.YELLOW)

    def _modal(self, title, geometry="620x420", minsize=None):
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.geometry(geometry)

        if minsize:
            dialog.minsize(*minsize)

        dialog.configure(bg=self.BG)

        try:
            if self.root.winfo_exists() and self.root.winfo_viewable():
                dialog.transient(self.root)

            dialog.update_idletasks()
            dialog.deiconify()

            try:
                dialog.geometry("+250+250")
            except tk.TclError:
                pass

            dialog.lift()
            dialog.focus_force()

            try:
                dialog.attributes("-topmost", True)

                def unset_topmost():
                    try:
                        dialog.attributes("-topmost", False)
                    except tk.TclError:
                        pass

                dialog.after(300, unset_topmost)
            except tk.TclError:
                pass

            dialog.grab_set()
        except tk.TclError:
            pass

        return dialog

    def _first_run_wizard(self):
        dialog = self._modal("First run setup", "700x480")

        state = {
            "page": 0,
            "profile": load_profiles().get("active", "Default"),
            "webhook": self.reader.get_discord_webhook_url() or "",
        }

        tk.Label(
            dialog,
            text="Chest Scanner setup",
            bg=self.BG,
            fg=self.TEXT,
            font=("Segoe UI", 19, "bold"),
        ).pack(anchor="w", padx=28, pady=(24, 5))

        tk.Label(
            dialog,
            text="First run: choose profile and configure Discord.",
            bg=self.BG,
            fg=self.MUTED,
            font=("Segoe UI", 10),
        ).pack(anchor="w", padx=28)

        body = tk.Frame(dialog, bg=self.BG)
        body.pack(fill="both", expand=True, padx=28, pady=22)

        nav = tk.Frame(dialog, bg=self.BG)
        nav.pack(fill="x", padx=28, pady=(0, 22))

        profile_var = tk.StringVar(value=state["profile"])
        webhook_var = tk.StringVar(value=state["webhook"])

        def clear_body():
            for child in body.winfo_children():
                child.destroy()

        def render_page(index):
            clear_body()
            state["page"] = index

            if index == 0:
                tk.Label(
                    body,
                    text="1. Profile",
                    bg=self.BG,
                    fg=self.ACCENT_HOVER,
                    font=("Segoe UI", 11, "bold"),
                ).pack(anchor="w")

                tk.Label(
                    body,
                    text="Profile name is used for separate items, results and Discord settings.",
                    bg=self.BG,
                    fg=self.TEXT,
                    wraplength=620,
                    justify="left",
                    font=("Segoe UI", 11),
                ).pack(anchor="w", pady=(12, 10))

                entry = tk.Entry(
                    body,
                    textvariable=profile_var,
                    bg=self.PANEL_2,
                    fg=self.TEXT,
                    insertbackground=self.TEXT,
                    relief="flat",
                    font=("Segoe UI", 11),
                )
                entry.pack(fill="x", ipady=9)
                entry.focus_set()

            elif index == 1:
                tk.Label(
                    body,
                    text="2. Discord Webhook",
                    bg=self.BG,
                    fg=self.ACCENT_HOVER,
                    font=("Segoe UI", 11, "bold"),
                ).pack(anchor="w")

                tk.Label(
                    body,
                    text="You can save webhook now or configure it later in Settings.",
                    bg=self.BG,
                    fg=self.TEXT,
                    wraplength=620,
                    justify="left",
                    font=("Segoe UI", 11),
                ).pack(anchor="w", pady=(12, 10))

                entry = tk.Entry(
                    body,
                    textvariable=webhook_var,
                    bg=self.PANEL_2,
                    fg=self.TEXT,
                    insertbackground=self.TEXT,
                    relief="flat",
                    font=("Segoe UI", 11),
                )
                entry.pack(fill="x", ipady=9)
                entry.focus_set()

            else:
                tk.Label(
                    body,
                    text="3. Done",
                    bg=self.BG,
                    fg=self.GREEN,
                    font=("Segoe UI", 11, "bold"),
                ).pack(anchor="w")

                tk.Label(
                    body,
                    text="Basic settings saved. You can change everything later from GUI.",
                    bg=self.BG,
                    fg=self.TEXT,
                    wraplength=620,
                    justify="left",
                    font=("Segoe UI", 11),
                ).pack(anchor="w", pady=(14, 6))

            back_btn.configure(state="normal" if index > 0 else "disabled")
            next_btn.configure(text="Done" if index == 2 else "Next")

        def finish_setup():
            name = profile_var.get().strip() or "Default"

            if any(char in name for char in '/\\:*?"<>|'):
                messagebox.showerror("Profile", "Invalid characters in profile name.", parent=dialog)
                return

            data = load_profiles()
            names = {entry.get("name") for entry in data.get("profiles", [])}

            if name not in names:
                profile_entry(name)

            activate_profile(name)
            self.reader.reload_profile()

            webhook = webhook_var.get().strip()
            if webhook:
                save_json(DISCORD_WEBHOOK_FILE, {"webhook_url": webhook})

            self.reader.config["profile_name"] = name
            self.reader.config["setup_complete"] = True
            save_json(CONFIG_FILE, self.reader.config)

            data = load_profiles()
            data["active"] = name
            data["setup_complete"] = True
            save_profiles(data)

            self._refresh_profile_label()
            self._refresh_stats()

            try:
                dialog.grab_release()
            except tk.TclError:
                pass

            dialog.destroy()
            self.root.deiconify()
            self._log("✓ First run setup completed.", self.GREEN)

        def go_next():
            if state["page"] == 2:
                finish_setup()
            else:
                render_page(state["page"] + 1)

        def go_back():
            if state["page"] > 0:
                render_page(state["page"] - 1)

        back_btn = tk.Button(
            nav,
            text="Back",
            command=go_back,
            bg=self.PANEL_2,
            fg=self.TEXT,
            activebackground=self.BORDER,
            activeforeground=self.TEXT,
            relief="flat",
            bd=0,
            padx=16,
            pady=8,
            font=("Segoe UI", 9, "bold"),
        )
        back_btn.pack(side="left")

        next_btn = tk.Button(
            nav,
            text="Next",
            command=go_next,
            bg=self.ACCENT,
            fg=self.TEXT,
            activebackground=self.ACCENT_HOVER,
            activeforeground=self.TEXT,
            relief="flat",
            bd=0,
            padx=20,
            pady=8,
            font=("Segoe UI", 9, "bold"),
        )
        next_btn.pack(side="right")

        dialog.bind("<Return>", lambda _e: go_next())

        render_page(0)
        self.root.wait_window(dialog)

        if self.running and self.root.winfo_exists():
            self.root.deiconify()

    def _prompt_unknown_item(self, unknown_name, image_path):
        result = {"name": ""}
        done = threading.Event()

        def show_dialog():
            dialog = self._modal("New item", "640x340")

            tk.Label(
                dialog,
                text="New item found",
                bg=self.BG,
                fg=self.TEXT,
                font=("Segoe UI", 17, "bold"),
            ).pack(anchor="w", padx=24, pady=(22, 4))

            body = tk.Frame(dialog, bg=self.BG)
            body.pack(fill="both", expand=True, padx=24, pady=(10, 0))

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
                text="No\nicon",
                bg=self.PANEL,
                fg=self.MUTED,
                font=("Segoe UI", 9),
            )
            icon_label.pack(expand=True)

            try:
                if image_path and os.path.isfile(image_path):
                    icon = tk.PhotoImage(file=image_path)

                    if icon.width() <= 48 and icon.height() <= 48:
                        icon = icon.zoom(3, 3)

                    icon_label.configure(image=icon, text="")
                    icon_label.image = icon
            except Exception:
                pass

            info = tk.Frame(body, bg=self.BG)
            info.pack(side="left", fill="both", expand=True)

            tk.Label(
                info,
                text=f"Template: {unknown_name}",
                bg=self.BG,
                fg=self.MUTED,
                font=("Segoe UI", 9),
            ).pack(anchor="w")

            tk.Label(
                info,
                text="Enter item name:",
                bg=self.BG,
                fg=self.TEXT,
                font=("Segoe UI", 10),
            ).pack(anchor="w", pady=(24, 6))

            entry = tk.Entry(
                info,
                bg=self.PANEL_2,
                fg=self.TEXT,
                insertbackground=self.TEXT,
                relief="flat",
                font=("Segoe UI", 11),
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
                buttons,
                text="Save",
                command=finish,
                bg=self.ACCENT,
                fg=self.TEXT,
                activebackground=self.ACCENT_HOVER,
                relief="flat",
                bd=0,
                padx=18,
                pady=8,
                font=("Segoe UI", 10, "bold"),
            ).pack(side="right")

            tk.Button(
                buttons,
                text="Keep unknown",
                command=finish,
                bg=self.PANEL_2,
                fg=self.TEXT,
                activebackground=self.BORDER,
                relief="flat",
                bd=0,
                padx=14,
                pady=8,
                font=("Segoe UI", 9),
            ).pack(side="right", padx=(0, 8))

            dialog.bind("<Return>", lambda _event: finish())
            dialog.bind("<Escape>", lambda _event: finish())
            dialog.protocol("WM_DELETE_WINDOW", finish)

        self.root.after(0, show_dialog)
        done.wait()

        return result["name"]

    def _save_webhook_from_entry(self, entry, dialog=None):
        url = entry.get().strip()

        if url and not url.startswith((
            "https://discord.com/api/webhooks/",
            "https://discordapp.com/api/webhooks/",
        )):
            messagebox.showerror("Discord", "URL does not look like Discord Webhook.", parent=dialog or self.root)
            return False

        if url:
            save_json(DISCORD_WEBHOOK_FILE, {"webhook_url": url})
        else:
            try:
                if os.path.exists(DISCORD_WEBHOOK_FILE):
                    os.remove(DISCORD_WEBHOOK_FILE)
            except OSError:
                pass

        return True

    def open_discord_settings(self, parent=None):
        if parent is not None:
            try:
                parent.grab_release()
            except tk.TclError:
                pass

        dialog = self._modal("Discord settings", "720x340")

        if parent is not None and parent.winfo_exists():
            try:
                dialog.transient(parent)
            except tk.TclError:
                pass

        tk.Label(
            dialog,
            text="Discord Webhook",
            bg=self.BG,
            fg=self.TEXT,
            font=("Segoe UI", 17, "bold"),
        ).pack(anchor="w", padx=24, pady=(22, 5))

        tk.Label(
            dialog,
            text="Webhook is stored separately for current profile.",
            bg=self.BG,
            fg=self.MUTED,
            font=("Segoe UI", 9),
        ).pack(anchor="w", padx=24)

        var = tk.StringVar(value=self.reader.get_discord_webhook_url() or "")

        entry = tk.Entry(
            dialog,
            textvariable=var,
            bg=self.PANEL_2,
            fg=self.TEXT,
            insertbackground=self.TEXT,
            relief="flat",
            font=("Segoe UI", 10),
        )
        entry.pack(fill="x", padx=24, pady=(20, 8), ipady=9)

        status_label = tk.Label(
            dialog,
            text="",
            bg=self.BG,
            fg=self.MUTED,
            font=("Segoe UI", 9),
            wraplength=640,
            justify="left",
        )
        status_label.pack(fill="x", padx=24, pady=(0, 8))

        buttons = tk.Frame(dialog, bg=self.BG)
        buttons.pack(fill="x", padx=24, pady=14)

        def restore_parent():
            if parent is not None and parent.winfo_exists():
                try:
                    parent.grab_set()
                    parent.lift()
                    parent.focus_force()
                except tk.TclError:
                    pass

        def close_dialog():
            try:
                dialog.grab_release()
            except tk.TclError:
                pass

            dialog.destroy()
            restore_parent()

        def save_and_close():
            if not self._save_webhook_from_entry(entry, dialog):
                return

            try:
                dialog.grab_release()
            except tk.TclError:
                pass

            dialog.destroy()
            restore_parent()
            self._log("✓ Discord settings saved.", self.GREEN)

        def test_webhook():
            url = var.get().strip()

            if not url or not url.startswith((
                "https://discord.com/api/webhooks/",
                "https://discordapp.com/api/webhooks/",
            )):
                status_label.configure(text="Enter valid Webhook URL.", fg=self.RED)
                return

            status_label.configure(text="Checking webhook...", fg=self.YELLOW)

            def worker():
                try:
                    response = requests.get(url, timeout=10)

                    if response.status_code == 200:
                        success = True
                        message = "Webhook is reachable."
                    else:
                        success = False
                        message = f"Discord returned HTTP {response.status_code}. Check URL."
                except requests.RequestException as error:
                    success = False
                    message = f"Network error: {error}"

                def apply_result():
                    if not dialog.winfo_exists():
                        return

                    status_label.configure(
                        text=message,
                        fg=self.GREEN if success else self.RED,
                    )

                self.root.after(0, apply_result)

            threading.Thread(target=worker, daemon=True).start()

        tk.Button(
            buttons,
            text="Check",
            command=test_webhook,
            bg=self.PANEL_2,
            fg=self.TEXT,
            activebackground=self.BORDER,
            activeforeground=self.TEXT,
            relief="flat",
            bd=0,
            padx=14,
            pady=8,
        ).pack(side="left")

        tk.Button(
            buttons,
            text="Close",
            command=close_dialog,
            bg=self.PANEL_2,
            fg=self.TEXT,
            activebackground=self.BORDER,
            activeforeground=self.TEXT,
            relief="flat",
            bd=0,
            padx=14,
            pady=8,
        ).pack(side="right", padx=(0, 8))

        tk.Button(
            buttons,
            text="Save",
            command=save_and_close,
            bg=self.ACCENT,
            fg=self.TEXT,
            activebackground=self.ACCENT_HOVER,
            activeforeground=self.TEXT,
            relief="flat",
            bd=0,
            padx=18,
            pady=8,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="right")

        dialog.protocol("WM_DELETE_WINDOW", close_dialog)

    def open_settings(self):
        dialog = self._modal("Settings", "760x560")

        tk.Label(
            dialog,
            text="Profile settings",
            bg=self.BG,
            fg=self.TEXT,
            font=("Segoe UI", 19, "bold"),
        ).pack(anchor="w", padx=24, pady=(22, 4))

        tk.Label(
            dialog,
            text="Changes are saved to config.json of active profile.",
            bg=self.BG,
            fg=self.MUTED,
            font=("Segoe UI", 9),
        ).pack(anchor="w", padx=24)

        body = tk.Frame(dialog, bg=self.BG)
        body.pack(fill="both", expand=True, padx=24, pady=18)

        labels = [
            ("Chest template threshold", "chest_template_threshold", 0.82),
            ("Empty slot threshold", "empty_slot_threshold", 1200),
            ("Item match threshold", "item_match_threshold", 2600),
            ("Scan interval, sec", "scan_interval_seconds", 2),
            ("Default stack size", "discord_stack_size", 64),
        ]

        vars_map = {}

        for row, (label, key, default) in enumerate(labels):
            tk.Label(
                body,
                text=label,
                bg=self.BG,
                fg=self.TEXT,
                font=("Segoe UI", 10),
            ).grid(row=row, column=0, sticky="w", pady=7)

            current = self.reader.config.get(key, default)

            if key == "discord_stack_size":
                current = self.reader.config.get("discord_message", {}).get("stack_size", default)

            var = tk.StringVar(value=str(current))
            vars_map[key] = var

            tk.Entry(
                body,
                textvariable=var,
                bg=self.PANEL_2,
                fg=self.TEXT,
                insertbackground=self.TEXT,
                relief="flat",
                width=18,
                font=("Segoe UI", 10),
            ).grid(row=row, column=1, sticky="e", padx=(20, 0), ipady=5)

        for col in range(2):
            body.grid_columnconfigure(col, weight=1)

        buttons = tk.Frame(dialog, bg=self.BG)
        buttons.pack(fill="x", padx=24, pady=(0, 20))

        def close_settings():
            try:
                dialog.grab_release()
            except tk.TclError:
                pass

            dialog.destroy()

        def save_settings():
            try:
                cfg = self.reader.config

                cfg["chest_template_threshold"] = float(vars_map["chest_template_threshold"].get())
                cfg["empty_slot_threshold"] = float(vars_map["empty_slot_threshold"].get())
                cfg["item_match_threshold"] = float(vars_map["item_match_threshold"].get())
                cfg["scan_interval_seconds"] = max(0.1, float(vars_map["scan_interval_seconds"].get()))
                cfg.setdefault("discord_message", {})["stack_size"] = max(1, int(vars_map["discord_stack_size"].get()))

                save_json(CONFIG_FILE, cfg)
                self.reader.reload_profile()

                try:
                    dialog.grab_release()
                except tk.TclError:
                    pass

                dialog.destroy()
                self._log("✓ Profile settings saved.", self.GREEN)

            except (TypeError, ValueError) as error:
                messagebox.showerror("Settings", f"Check numeric values: {error}", parent=dialog)

        self._button(buttons, "Discord", lambda: self.open_discord_settings(dialog)).pack(side="left")
        self._button(buttons, "Save", save_settings, True).pack(side="right")
        self._button(buttons, "Close", close_settings).pack(side="right", padx=7)

    def open_item_database(self):
        dialog = self._modal("Item database", "1000x650", (900, 560))

        tk.Label(
            dialog,
            text="Item database",
            bg=self.BG,
            fg=self.TEXT,
            font=("Segoe UI", 19, "bold"),
        ).pack(anchor="w", padx=20, pady=(18, 4))

        top = tk.Frame(dialog, bg=self.BG)
        top.pack(fill="x", padx=20, pady=(0, 10))

        search_var = tk.StringVar()

        tk.Label(
            top,
            text="Search",
            bg=self.BG,
            fg=self.MUTED,
            font=("Segoe UI", 9),
        ).pack(side="left")

        search = tk.Entry(
            top,
            textvariable=search_var,
            bg=self.PANEL_2,
            fg=self.TEXT,
            insertbackground=self.TEXT,
            relief="flat",
            font=("Segoe UI", 10),
        )
        search.pack(side="left", fill="x", expand=True, padx=(10, 0), ipady=6)

        content = tk.Frame(dialog, bg=self.BG)
        content.pack(fill="both", expand=True, padx=20, pady=(0, 12))

        left = tk.Frame(content, bg=self.PANEL, highlightbackground=self.BORDER, highlightthickness=1)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))

        tree = ttk.Treeview(
            left,
            columns=("name", "stack", "photos", "enabled"),
            show="headings",
            selectmode="browse",
        )

        for col, title, width in (
            ("name", "Name", 380),
            ("stack", "Stack", 70),
            ("photos", "Photos", 70),
            ("enabled", "Enabled", 70),
        ):
            tree.heading(col, text=title)
            tree.column(col, width=width, anchor="w" if col == "name" else "center")

        tree.pack(fill="both", expand=True, side="left")

        yscroll = tk.Scrollbar(left, command=tree.yview)
        yscroll.pack(side="right", fill="y")
        tree.configure(yscrollcommand=yscroll.set)

        right = tk.Frame(
            content,
            bg=self.PANEL,
            highlightbackground=self.BORDER,
            highlightthickness=1,
            width=320,
        )
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        preview = tk.Label(
            right,
            text="Select item",
            bg=self.PANEL,
            fg=self.MUTED,
            font=("Segoe UI", 10),
        )
        preview.pack(fill="x", pady=(18, 8), padx=14)

        details = tk.Label(
            right,
            text="",
            bg=self.PANEL,
            fg=self.TEXT,
            justify="left",
            font=("Segoe UI", 9),
            wraplength=280,
        )
        details.pack(fill="x", padx=14)

        photo_list = tk.Listbox(
            right,
            bg=self.PANEL_2,
            fg=self.TEXT,
            relief="flat",
            highlightthickness=0,
            height=9,
        )
        photo_list.pack(fill="both", expand=True, padx=14, pady=14)

        state = {"selected_id": None}

        def get_items():
            return self.reader.items_db.get("items", [])

        def find_item(item_id):
            return next((item for item in get_items() if str(item.get("id")) == str(item_id)), None)

        def refresh():
            selected = state.get("selected_id")

            for iid in tree.get_children():
                tree.delete(iid)

            q = search_var.get().strip().lower()

            for item in sorted(get_items(), key=lambda x: x.get("name", "").lower()):
                if q and q not in item.get("name", "").lower():
                    continue

                images = item.get("images") or ([item.get("image")] if item.get("image") else [])

                tree.insert(
                    "",
                    "end",
                    iid=str(item["id"]),
                    values=(
                        item.get("name", ""),
                        item.get("stack_size", 64),
                        len(images),
                        "Yes" if item.get("enabled", True) else "No",
                    ),
                )

            if selected and tree.exists(str(selected)):
                tree.selection_set(str(selected))
                tree.focus(str(selected))
                show_selected()

        def show_selected(_event=None):
            selection = tree.selection()

            if not selection:
                state["selected_id"] = None
                preview.configure(text="Select item", image="")
                details.configure(text="")
                photo_list.delete(0, "end")
                return

            item = find_item(selection[0])
            if item is None:
                return

            state["selected_id"] = item["id"]
            images = item.get("images") or ([item.get("image")] if item.get("image") else [])

            details.configure(
                text=(
                    f"Name: {item.get('name', '')}\n"
                    f"Stack size: {item.get('stack_size', 64)}\n"
                    f"Photos: {len(images)}\n"
                    f"Enabled: {'Yes' if item.get('enabled', True) else 'No'}"
                )
            )

            photo_list.delete(0, "end")

            for path in images:
                photo_list.insert("end", path)

            if images:
                try:
                    image = tk.PhotoImage(file=absolute_path(images[0]))
                    scale = max(1, image.width() // 96, image.height() // 96)

                    if scale > 1:
                        image = image.subsample(scale, scale)

                    preview.configure(image=image, text="")
                    preview.image = image
                except Exception:
                    preview.configure(text="Cannot show photo", image="")
            else:
                preview.configure(text="No photos", image="")

        tree.bind("<<TreeviewSelect>>", show_selected)
        search_var.trace_add("write", lambda *_: refresh())

        def save_db():
            self.reader.items_db = normalize_items_db(self.reader.items_db)
            save_json(ITEMS_FILE, self.reader.items_db)
            self.reader.items_db = normalize_items_db(load_json(ITEMS_FILE, {"items": []}))
            self.reader._rebuild_stack_size_cache()
            refresh()
            self._refresh_stats()

        def add_item():
            name = simpledialog.askstring("New item", "Item name:", parent=dialog)
            if not name or not name.strip():
                return

            name = name.strip()

            stack_text = simpledialog.askstring("New item", "Stack size:", initialvalue="64", parent=dialog)

            try:
                stack_size = max(1, int(stack_text or "64"))
            except ValueError:
                stack_size = 64

            paths = filedialog.askopenfilenames(
                parent=dialog,
                title="Select item photos",
                filetypes=[("PNG/JPG", "*.png *.jpg *.jpeg"), ("All files", "*.*")],
            )

            item = {
                "id": str(uuid.uuid4()),
                "name": name,
                "stack_size": stack_size,
                "images": [],
                "image": "",
                "aliases": [],
                "enabled": True,
            }

            for source in paths:
                rel = next_image_path(name, ".png")
                copy_image_as_png(source, rel)
                item["images"].append(rel)

            if item["images"]:
                item["image"] = item["images"][0]

            self.reader.items_db.setdefault("items", []).append(item)
            save_db()
            tree.selection_set(item["id"])

        def edit_item():
            item = find_item(state.get("selected_id"))

            if item is None:
                messagebox.showinfo("Item database", "Select item first.", parent=dialog)
                return

            old_name = item.get("name", "item")

            name = simpledialog.askstring("Edit item", "Name:", initialvalue=old_name, parent=dialog)
            if name is None:
                return

            stack_text = simpledialog.askstring(
                "Edit item",
                "Stack size:",
                initialvalue=str(item.get("stack_size", 64)),
                parent=dialog,
            )

            try:
                stack_size = max(1, int(stack_text or item.get("stack_size", 64)))
            except ValueError:
                stack_size = item.get("stack_size", 64)

            new_name = name.strip() or old_name

            if new_name != old_name:
                images = item.get("images") or []
                new_images = []

                for old_rel in images:
                    ext = os.path.splitext(old_rel)[1] or ".png"
                    new_rel = next_image_path(new_name, ext)

                    old_full = absolute_path(old_rel)
                    new_full = absolute_path(new_rel)

                    try:
                        if old_full and os.path.isfile(old_full):
                            if os.path.abspath(old_full) != os.path.abspath(new_full):
                                shutil.move(old_full, new_full)

                        new_images.append(new_rel)
                    except OSError:
                        new_images.append(old_rel)

                item["images"] = new_images
                item["image"] = new_images[0] if new_images else ""

            item["name"] = new_name
            item["stack_size"] = stack_size

            save_db()
            show_selected()

        def toggle_item():
            item = find_item(state.get("selected_id"))
            if item is None:
                return

            item["enabled"] = not item.get("enabled", True)
            save_db()
            show_selected()

        def delete_item():
            item = find_item(state.get("selected_id"))
            if item is None:
                return

            if not messagebox.askyesno("Delete", f"Delete '{item.get('name', '')}' from database?", parent=dialog):
                return

            self.reader.items_db["items"] = [x for x in get_items() if x.get("id") != item.get("id")]
            state["selected_id"] = None
            save_db()

        def add_photo():
            item = find_item(state.get("selected_id"))

            if item is None:
                messagebox.showinfo("Photos", "Select item first.", parent=dialog)
                return

            paths = filedialog.askopenfilenames(
                parent=dialog,
                title="Add photos",
                filetypes=[("Images", "*.png *.jpg *.jpeg"), ("All files", "*.*")],
            )

            images = item.get("images") or ([item.get("image")] if item.get("image") else [])

            for source in paths:
                rel = next_image_path(item.get("name", "item"), ".png")
                copy_image_as_png(source, rel)
                images.append(rel)

            item["images"] = images
            item["image"] = images[0] if images else ""

            save_db()
            show_selected()

        def remove_photo():
            item = find_item(state.get("selected_id"))
            sel = photo_list.curselection()

            if item is None or not sel:
                return

            images = item.get("images") or []
            index = sel[0]

            if index >= len(images):
                return

            path = images.pop(index)

            try:
                full = absolute_path(path)
                if os.path.isfile(full):
                    os.remove(full)
            except OSError:
                pass

            item["images"] = images
            item["image"] = images[0] if images else ""

            save_db()
            show_selected()

        def rename_photo():
            item = find_item(state.get("selected_id"))
            sel = photo_list.curselection()

            if item is None or not sel:
                return

            images = item.get("images") or []
            path = images[sel[0]]

            new_rel = next_image_path(item.get("name", "item"), os.path.splitext(path)[1])

            try:
                shutil.move(absolute_path(path), absolute_path(new_rel))

                images[sel[0]] = new_rel
                item["images"] = images
                item["image"] = images[0] if images else ""

                save_db()
                show_selected()
            except OSError as error:
                messagebox.showerror("Photo", str(error), parent=dialog)

        bottom = tk.Frame(dialog, bg=self.BG)
        bottom.pack(fill="x", padx=20, pady=(0, 18))

        self._button(bottom, "Add", add_item, True).pack(side="left", padx=3)
        self._button(bottom, "Edit", edit_item).pack(side="left", padx=3)
        self._button(bottom, "Toggle", toggle_item).pack(side="left", padx=3)
        self._button(bottom, "Delete", delete_item).pack(side="left", padx=3)
        self._button(bottom, "Add photo", add_photo).pack(side="left", padx=3)
        self._button(bottom, "Remove photo", remove_photo).pack(side="left", padx=3)
        self._button(bottom, "Rename photo", rename_photo).pack(side="left", padx=3)
        self._button(bottom, "Close", dialog.destroy).pack(side="right", padx=3)

        refresh()

    def open_profile_manager(self):
        if self.busy:
            self._log("Cannot switch profile during operation.", self.YELLOW)
            return

        dialog = self._modal("Profiles", "700x500")

        tk.Label(
            dialog,
            text="Profiles",
            bg=self.BG,
            fg=self.TEXT,
            font=("Segoe UI", 19, "bold"),
        ).pack(anchor="w", padx=24, pady=(20, 4))

        body = tk.Frame(dialog, bg=self.BG)
        body.pack(fill="both", expand=True, padx=24, pady=16)

        listbox = tk.Listbox(
            body,
            bg=self.PANEL,
            fg=self.TEXT,
            selectbackground="#29304a",
            relief="flat",
            highlightthickness=0,
            font=("Segoe UI", 11),
        )
        listbox.pack(fill="both", expand=True, side="left")

        button_panel = tk.Frame(body, bg=self.BG)
        button_panel.pack(side="right", fill="y", padx=(12, 0))

        profiles = load_profiles()
        selected_name = profiles.get("active", "Default")

        def refresh_profiles():
            listbox.delete(0, "end")

            for entry in profiles.get("profiles", []):
                marker = "  ●" if entry.get("name") == profiles.get("active") else ""
                listbox.insert("end", entry.get("name", "Profile") + marker)

            for i, entry in enumerate(profiles.get("profiles", [])):
                if entry.get("name") == selected_name:
                    listbox.selection_set(i)

        def chosen():
            sel = listbox.curselection()
            if not sel:
                return None

            return listbox.get(sel[0]).replace("  ●", "")

        def switch_profile():
            nonlocal selected_name

            name = chosen()
            if not name:
                return

            if name == profiles.get("active"):
                dialog.destroy()
                return

            if not messagebox.askyesno("Profile", f"Switch to '{name}'?", parent=dialog):
                return

            activate_profile(name)
            self.reader.reload_profile()
            self.reader.unknown_item_prompt = self._prompt_unknown_item

            self._refresh_profile_label()
            self._refresh_stats()

            self._log(f"✓ Active profile: {name}", self.GREEN)

            selected_name = name
            dialog.destroy()

        def create_profile():
            name = simpledialog.askstring("New profile", "Name:", parent=dialog)
            if not name or not name.strip():
                return

            name = name.strip()

            if any(c in name for c in '/\\:*?"<>|'):
                messagebox.showerror("Profile", "Invalid characters.", parent=dialog)
                return

            if any(x.get("name") == name for x in profiles.get("profiles", [])):
                messagebox.showerror("Profile", "Profile already exists.", parent=dialog)
                return

            current_dir = PROFILE_DIR
            entry = profile_entry(name)

            new_dir = os.path.join(BASE_DIR, entry["directory"]) if entry.get("directory") != "." else BASE_DIR
            os.makedirs(new_dir, exist_ok=True)

            try:
                src_config = os.path.join(current_dir, "config.json")
                src_items = os.path.join(current_dir, "items.json")

                if os.path.isfile(src_config):
                    shutil.copy2(src_config, os.path.join(new_dir, "config.json"))

                if os.path.isfile(src_items):
                    shutil.copy2(src_items, os.path.join(new_dir, "items.json"))

                src_images = os.path.join(current_dir, "items")

                if os.path.isdir(src_images):
                    shutil.copytree(src_images, os.path.join(new_dir, "items"), dirs_exist_ok=True)

            except OSError as error:
                messagebox.showerror("Profile", f"Cannot create profile files: {error}", parent=dialog)
                return

            profiles["profiles"] = load_profiles().get("profiles", [])
            refresh_profiles()

        def delete_profile():
            name = chosen()

            if not name or name == "Default":
                messagebox.showinfo("Profile", "Default profile cannot be deleted.", parent=dialog)
                return

            if name == profiles.get("active"):
                messagebox.showinfo("Profile", "Switch to another profile first.", parent=dialog)
                return

            if not messagebox.askyesno("Delete", f"Delete profile '{name}' and its data?", parent=dialog):
                return

            entry = next((x for x in profiles.get("profiles", []) if x.get("name") == name), None)

            if entry:
                path = os.path.join(BASE_DIR, entry.get("directory", "profiles"))

                try:
                    shutil.rmtree(path)
                except OSError:
                    pass

            profiles["profiles"] = [x for x in profiles.get("profiles", []) if x.get("name") != name]
            save_profiles(profiles)
            refresh_profiles()

        for text, cmd, accent in (
            ("Switch", switch_profile, True),
            ("New", create_profile, False),
            ("Delete", delete_profile, False),
        ):
            self._button(button_panel, text, cmd, accent).pack(fill="x", pady=4)

        self._button(button_panel, "Close", dialog.destroy).pack(fill="x", pady=(18, 4))

        refresh_profiles()

    def _refresh_profile_label(self):
        profiles = load_profiles()
        self.profile_label.configure(text=f"Profile: {profiles.get('active', 'Default')}")

    def _scan(self):
        result = self.reader.scan_chest(force=True)

        if result is not None:
            self._log("✓ Chest scanned.", self.GREEN)

            timestamp = result.get("timestamp", "—")
            occupied_slots = result.get("occupied_slots", 0)
            totals = result.get("totals", {}) or {}
            unknown_items = set(result.get("unknown_items", []) or [])

            self._log("┌─ Last chest contents", self.ACCENT)
            self._log(f"│ Time: {timestamp}", self.MUTED)
            self._log(f"│ Occupied slots: {occupied_slots}", self.MUTED)

            if totals:
                for item_name, quantity in sorted(totals.items(), key=lambda x: x[0].lower()):
                    suffix = "  [unknown]" if item_name in unknown_items else ""
                    stack_size = self.reader.get_item_stack_size(item_name)
                    self._log(f"│ {item_name}: {quantity} (stack: {stack_size}){suffix}")
            else:
                self._log("│ Chest is empty.", self.MUTED)

            self._log("└─ End", self.ACCENT)
        else:
            self._log("Chest not found or no changes.", self.MUTED)

        self._refresh_stats()

    def _discord(self):
        success, message = self.reader.send_all_chests_to_discord()

        if success:
            self._log(f"✓ {message}", self.GREEN)
        else:
            self._log(f"✗ Discord: {message}", self.RED)
            messagebox.showerror("Discord error", message)

        self._refresh_stats()

    def _run(self, title, func):
        if self.busy:
            self._log("Wait: previous operation is still running.", self.YELLOW)
            return

        def worker():
            self.busy = True

            self.root.after(0, lambda: self.status_dot.configure(text="●  RUNNING", fg=self.YELLOW))
            self._log(f"▶ {title}...")

            try:
                func()
                self._log(f"✓ {title}: done.", self.GREEN)
            except Exception as error:
                self._log(f"✕ {title}: {type(error).__name__}: {error}", self.RED)
            finally:
                self.busy = False
                self.root.after(0, lambda: self.status_dot.configure(text="●  READY", fg=self.GREEN))
                self.root.after(0, self._refresh_stats)

        threading.Thread(target=worker, daemon=True).start()

    def _keyboard_loop(self):
        if not self.running:
            return

        if not KEYBOARD_AVAILABLE:
            return

        try:
            if keyboard.is_pressed("f6"):
                self._run("Digits debug", self.reader.save_quantity_debug)
                time.sleep(0.35)
            elif keyboard.is_pressed("f7"):
                self._run("Grid debug", self.reader.show_grid_debug)
                time.sleep(0.35)
            elif keyboard.is_pressed("f8"):
                self._run("Scan", self._scan)
                time.sleep(0.35)
            elif keyboard.is_pressed("f9"):
                self._run("Discord send", self._discord)
                time.sleep(0.35)
            elif keyboard.is_pressed("f10"):
                self.close()
                return
        except Exception as error:
            self._log(f"Hotkey error: {error}", self.RED)

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
                self.last_scan_label.configure(text=f"Last action: {message[10:]}")
        except Empty:
            pass

        if self.running:
            self.root.after(100, self._poll_logs)

    def close(self):
        if not self.running:
            return

        self.running = False

        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self):
        self.root.mainloop()


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