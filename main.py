import os
import cv2
import json
import time
import uuid
import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

import keyboard
import numpy as np
import pyautogui
import requests

import winsound
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from queue import Queue, Empty

# ============================================================
# ПУТИ
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
ITEMS_FILE = os.path.join(BASE_DIR, "items.json")

ASSETS_DIR = os.path.join(BASE_DIR, "assets")
DIGITS_DIR = os.path.join(ASSETS_DIR, "digits")

RESULTS_DIR = os.path.join(BASE_DIR, "results")
UNKNOWN_DIR = os.path.join(BASE_DIR, "unknown_items")

RESULTS_FILE = os.path.join(RESULTS_DIR, "chests.json")
HISTORY_DIR = os.path.join(RESULTS_DIR, "history")
DISCORD_MESSAGE_IDS_FILE = os.path.join(RESULTS_DIR, "discord_message_ids.json")


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
    os.makedirs(DIGITS_DIR, exist_ok=True)
    os.makedirs(RESULTS_DIR, exist_ok=True)
    os.makedirs(HISTORY_DIR, exist_ok=True)
    os.makedirs(UNKNOWN_DIR, exist_ok=True)


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
            item_image = item.get("image", "")

            if (
                item_name == unknown_name
                or item_image == relative_path
                or os.path.normpath(
                    absolute_path(item_image)
                ) == os.path.normpath(full_path)
            ):
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
                "name": final_name,
                "image": relative_path
            }

            self.items_db.setdefault("items", []).append(existing_item)

        else:
            existing_item["name"] = final_name
            existing_item["image"] = relative_path

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

        webhook_file = os.path.join(
            BASE_DIR,
            "discord_webhook.json"
        )

        # Основной способ: отдельный JSON-файл,
        # который создаётся Запуск.vbs.
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

        self.reader.unknown_item_prompt = self._prompt_unknown_item

        self._setup_style()
        self._build_ui()
        self._refresh_stats()
        self._poll_logs()
        self._keyboard_loop()

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
        actions.pack(fill="x", padx=28, pady=(0, 18))
        self._button(actions, "Сканировать", lambda: self._run("Сканирование", self._scan), True).pack(side="left", padx=(0, 8))
        self._button(actions, "Discord", lambda: self._run("Отправка в Discord", self._discord)).pack(side="left", padx=4)
        self._button(actions, "Сетка F7", lambda: self._run("Отладка сетки", self.reader.show_grid_debug)).pack(side="left", padx=4)
        self._button(actions, "Цифры F6", lambda: self._run("Отладка цифр", self.reader.save_quantity_debug)).pack(side="left", padx=4)
        self._button(actions, "Выход", self.close).pack(side="right")

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
    main()
