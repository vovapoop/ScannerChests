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

from PIL import Image, ImageDraw, ImageFont

import winsound

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

DISCORD_TABLE_IMAGE_FILE = os.path.join(RESULTS_DIR, "discord_items_table.png")
DISCORD_TABLE_IMAGE_PREFIX = os.path.join(RESULTS_DIR, "discord_items_table_part")
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
    "discord_delete_old_messages": True,

    # Формат сообщения Discord. Все эти параметры можно менять в config.json.
    "discord_message": {
        "header": "",
        "scanned_chests": "Отсканировано сундуков: **{chests}**",
        "last_scan": "Время последнего сканирования: **{last_scan}**",
        "items_title": "══════════Предметы══════════",
        "columns": "Иконка | Название | Всего | Стаков",
        "item": "{icon} | {name} | {total} | {stacks}",
        "item_icon": "📦",
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

    def play_success_sound(self):
        """
        Проигрывает звук после успешного сканирования.
        Работает в Windows.
        """
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
            user_name = input(f"Название для {unknown_name}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nВвод отменён. Будет использовано имя:")
            print(unknown_name)
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
            "timestamp": datetime.now(ZoneInfo("Europe/Moscow")).isoformat(timespec="seconds"),
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

    def get_item_image_path(self, item_name):
        """Возвращает путь к иконке предмета из items.json."""
        for item in self.items_db.get("items", []):
            if item.get("name") != item_name:
                continue

            image_paths = item.get("images")
            if not image_paths:
                image_paths = [item.get("image")]

            for image_path in image_paths:
                if not image_path:
                    continue

                full_path = absolute_path(image_path)
                if full_path and os.path.exists(full_path):
                    return full_path

        return None

    @staticmethod
    def load_font(size, bold=False):
        """Загружает шрифт для таблицы с поддержкой кириллицы."""
        candidates = []

        if os.name == "nt":
            candidates.extend([
                r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
                r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
            ])

        candidates.extend([
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        ])

        for path in candidates:
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, size=size)
                except OSError:
                    pass

        return ImageFont.load_default()

    def build_discord_items_images(self, sorted_items, stack_size, table_config):
        """Создаёт несколько PNG-частей таблицы для Discord."""
        icon_size = max(24, int(table_config.get("image_icon_size", 40)))
        row_height = max(icon_size + 10, int(table_config.get("image_row_height", 52)))
        padding = max(8, int(table_config.get("image_padding", 12)))
        font_size = max(10, int(table_config.get("image_font_size", 18)))
        header_font_size = max(10, int(table_config.get("image_header_font_size", 16)))

        background = tuple(table_config.get("image_background", [32, 34, 37]))
        header_background = tuple(table_config.get("image_header_background", [47, 49, 54]))
        text_color = tuple(table_config.get("image_text_color", [255, 255, 255]))
        muted_color = tuple(table_config.get("image_muted_color", [185, 187, 190]))
        border_color = tuple(table_config.get("image_border_color", [79, 84, 92]))

        icon_column = max(70, int(table_config.get("image_icon_column", 70)))
        name_column = max(220, int(table_config.get("image_name_column", 430)))
        total_column = max(110, int(table_config.get("image_total_column", 140)))
        stacks_column = max(100, int(table_config.get("image_stacks_column", 120)))
        rows_per_part = max(1, int(table_config.get("image_rows_per_part", 20)))

        width = padding * 2 + icon_column + name_column + total_column + stacks_column
        header_height = max(row_height, 48)
        image_paths = []

        for part_index, start_index in enumerate(
            range(0, len(sorted_items), rows_per_part), start=1
        ):
            part_items = sorted_items[start_index:start_index + rows_per_part]
            height = padding * 2 + header_height + row_height * len(part_items)
            image = Image.new("RGB", (width, height), background)
            draw = ImageDraw.Draw(image)

            font = self.load_font(font_size)
            header_font = self.load_font(header_font_size, bold=True)

            draw.rectangle(
                [padding, padding, width - padding, padding + header_height],
                fill=header_background
            )

            x_icon = padding
            x_name = x_icon + icon_column
            x_total = x_name + name_column
            x_stacks = x_total + total_column

            header_y = padding + (header_height - header_font_size) // 2 - 2
            draw.text((x_icon + 10, header_y), "Иконка", font=header_font, fill=text_color)
            draw.text((x_name + 10, header_y), "Название", font=header_font, fill=text_color)
            draw.text((x_total + 10, header_y), "Всего", font=header_font, fill=text_color)
            draw.text((x_stacks + 10, header_y), "Стаков", font=header_font, fill=text_color)

            draw.line(
                [padding, padding + header_height, width - padding, padding + header_height],
                fill=border_color,
                width=1
            )

            for index, (item_name, quantity) in enumerate(part_items):
                y1 = padding + header_height + index * row_height
                y2 = y1 + row_height

                if index % 2 == 1:
                    row_bg = tuple(min(255, value + 5) for value in background)
                    draw.rectangle([padding, y1, width - padding, y2], fill=row_bg)

                for x in (x_name, x_total, x_stacks):
                    draw.line([x, y1, x, y2], fill=border_color, width=1)

                icon_path = self.get_item_image_path(item_name)
                if icon_path:
                    try:
                        icon = Image.open(icon_path).convert("RGBA")
                        icon.thumbnail((icon_size, icon_size), Image.Resampling.LANCZOS)
                        icon_x = x_icon + (icon_column - icon.width) // 2
                        icon_y = y1 + (row_height - icon.height) // 2
                        image.paste(icon, (icon_x, icon_y), icon)
                    except (OSError, ValueError):
                        draw.text(
                            (x_icon + 20, y1 + (row_height - font_size) // 2 - 2),
                            "?", font=font, fill=muted_color
                        )
                else:
                    draw.text(
                        (x_icon + 20, y1 + (row_height - font_size) // 2 - 2),
                        "?", font=font, fill=muted_color
                    )

                if stack_size and quantity < stack_size:
                    stacks_text = ""
                else:
                    stacks = quantity / stack_size if stack_size else quantity
                    if stacks == int(stacks):
                        stacks_text = str(int(stacks))
                    else:
                        stacks_text = f"{stacks:.2f}".rstrip("0").rstrip(".")

                name = str(item_name)
                max_name_width = name_column - 20
                while name and draw.textbbox((0, 0), name, font=font)[2] > max_name_width:
                    name = name[:-2] + "…"

                text_y = y1 + (row_height - font_size) // 2 - 3
                draw.text((x_name + 10, text_y), name, font=font, fill=text_color)

                total_text = self.format_number(quantity)
                total_bbox = draw.textbbox((0, 0), total_text, font=font)
                draw.text(
                    (x_total + total_column - 10 - (total_bbox[2] - total_bbox[0]), text_y),
                    total_text, font=font, fill=text_color
                )

                stacks_bbox = draw.textbbox((0, 0), stacks_text, font=font)
                draw.text(
                    (x_stacks + stacks_column - 10 - (stacks_bbox[2] - stacks_bbox[0]), text_y),
                    stacks_text, font=font, fill=text_color
                )

                draw.line([padding, y2, width - padding, y2], fill=border_color, width=1)

            image_path = f"{DISCORD_TABLE_IMAGE_PREFIX}_{part_index}.png"
            image.save(image_path, "PNG", optimize=True)
            image_paths.append(image_path)

        return image_paths

    def build_discord_message(self, chests):
        """
        Формирует сообщение Discord с аккуратно выровненной таблицей.

        В текстовой версии остаётся таблица для поиска Discord.
        Дополнительно send_all_chests_to_discord() прикрепляет PNG
        с настоящими иконками каждого предмета.
        """
        if not chests:
            return (
                "📦 **База сундуков пуста**\n"
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
                if width <= 1:
                    return text[:width]
                text = text[:width - 1] + "…"
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

        sorted_items = sorted(
            global_totals.items(), key=lambda item: item[0].lower()
        )

        item_lines = []
        for item_name, quantity in sorted_items:
            if stack_size and quantity < stack_size:
                stacks_text = ""
            else:
                stacks = quantity / stack_size if stack_size else quantity
                if stacks == int(stacks):
                    stacks_text = str(int(stacks))
                else:
                    stacks_text = f"{stacks:.2f}".rstrip("0").rstrip(".")

            item_lines.append(
                table_line(
                    item_name,
                    self.format_number(quantity),
                    stacks_text
                )
            )

        table = "\n".join([columns, separator] + item_lines)
        items_text = f"```text\n{table}\n```"

        unknown_template = discord_config.get("unknown_item", "• {name}")
        unknowns_text = discord_config.get("item_separator", "\n").join(
            unknown_template.format(name=name)
            for name in sorted(all_unknown_items)
        )

        values = {
            "chests": len(chests),
            "last_scan": last_timestamp,
            "items": items_text,
            "unknowns": unknowns_text,
        }

        header = discord_config.get("header", "")
        scanned_chests = discord_config.get(
            "scanned_chests", "Отсканировано сундуков: {chests}"
        ).format(**values)
        last_scan = discord_config.get(
            "last_scan", "Время последнего сканирования: {last_scan}"
        ).format(**values)
        items_title = discord_config.get("items_title", "══════════Предметы══════════")

        lines = []
        if header:
            lines.extend([header, ""])

        lines.extend([
            scanned_chests,
            last_scan,
            "",
            items_title,
            "",
            items_text
        ])

        if all_unknown_items:
            unknown_title = discord_config.get(
                "unknown_title", "══════════Неизвестные предметы══════════"
            )
            lines.extend(["", unknown_title, "", unknowns_text])

        return "\n".join(lines)


    def delete_old_discord_messages(self, webhook_url):
        """Удаляет предыдущие сообщения, отправленные этим webhook."""
        if not self.config.get("discord_delete_old_messages", True):
            return

        data = load_json(DISCORD_MESSAGE_IDS_FILE, {"message_ids": []})
        message_ids = data.get("message_ids", [])

        if not message_ids:
            return

        deleted = 0
        failed = 0

        for message_id in message_ids:
            try:
                response = requests.delete(
                    f"{webhook_url}/messages/{message_id}",
                    timeout=15
                )

                if response.status_code in (200, 204, 404):
                    # 404 означает, что сообщение уже удалено — это нормально.
                    deleted += 1
                else:
                    failed += 1
                    print(
                        f"[DISCORD] Не удалось удалить старое сообщение "
                        f"{message_id}: {response.status_code} {response.text}"
                    )

            except requests.RequestException as error:
                failed += 1
                print(
                    f"[DISCORD] Ошибка удаления сообщения "
                    f"{message_id}: {error}"
                )

        # Не оставляем удалённые ID в истории.
        save_json(DISCORD_MESSAGE_IDS_FILE, {"message_ids": []})

        print(
            f"[DISCORD] Старые сообщения: удалено {deleted}"
            + (f", ошибок {failed}" if failed else "")
        )

    @staticmethod
    def save_discord_message_ids(message_ids):
        """Сохраняет ID сообщений, отправленных текущим отчётом."""
        save_json(
            DISCORD_MESSAGE_IDS_FILE,
            {"message_ids": [str(message_id) for message_id in message_ids]}
        )

    def send_all_chests_to_discord(self):
        """
        Отправляет отчёт в Discord несколькими сообщениями,
        если он превышает лимит 2000 символов.
        JSON-файл в Discord не отправляется.
        """
        env_name = self.config.get(
            "discord_webhook_env",
            "DISCORD_WEBHOOK_URL"
        )

        webhook_url = os.getenv(env_name)

        if not webhook_url:
            print(
                f"[DISCORD] Не задана переменная окружения {env_name}."
            )
            return

        if not os.path.exists(RESULTS_FILE):
            print(
                "[DISCORD] Файл chests.json не найден. "
                "Сначала выполните сканирование."
            )
            return

        data = load_json(
            RESULTS_FILE,
            {"chests": []}
        )

        chests = data.get("chests", [])

        if not chests:
            print("[DISCORD] Нет сохранённых сканирований.")
            return

        # Перед новым отчётом удаляем предыдущие сообщения этого webhook.
        self.delete_old_discord_messages(webhook_url)

        full_message = self.build_discord_message(chests)

        # Создаём отдельную PNG-таблицу с реальными иконками предметов.
        discord_config = self.config.get("discord_message", {})
        table_config = discord_config.get("table", {})
        global_totals = {}
        for chest in chests:
            for item_name, quantity in chest.get("totals", {}).items():
                global_totals[item_name] = global_totals.get(item_name, 0) + quantity

        sorted_items = sorted(
            global_totals.items(), key=lambda item: item[0].lower()
        )
        table_image_paths = []
        if sorted_items:
            try:
                table_config = dict(table_config)
                table_config.setdefault(
                    "image_rows_per_part",
                    self.config.get("discord_image_rows_per_part", 20)
                )
                table_image_paths = self.build_discord_items_images(
                    sorted_items,
                    discord_config.get("stack_size", 64),
                    table_config
                )
            except Exception as error:
                print(f"[DISCORD] Не удалось создать таблицы с иконками: {error}")

        # В Discord максимум 2000 символов.
        # Используем 1900, чтобы оставить запас.
        message_parts = split_discord_message(
            full_message,
            limit=1900
        )

        if not message_parts:
            message_parts = [
                "📦 Отчёт пуст."
            ]

        total_parts = len(message_parts)

        try:
            sent_message_ids = []

            # Объединяем части текстового отчёта с частями PNG-таблицы:
            # одно сообщение Discord = текстовая часть + соответствующая картинка.
            paired_count = max(len(message_parts), len(table_image_paths), 1)

            for index in range(paired_count):
                if index == 0:
                    content = message_parts[0] if message_parts else "📦 Отчёт пуст."
                else:
                    header = (
                        f"📦 **Продолжение отчёта "
                        f"({index + 1}/{len(message_parts)})**\n\n"
                    )
                    part = message_parts[index] if index < len(message_parts) else ""
                    available_length = 1900 - len(header)
                    content = header + part[:max(1, available_length)]

                content = content[:1900]

                image_file = None
                try:
                    files = {}
                    if index < len(table_image_paths) and os.path.exists(table_image_paths[index]):
                        image_file = open(table_image_paths[index], "rb")
                        files["table_image"] = (
                            os.path.basename(table_image_paths[index]),
                            image_file,
                            "image/png"
                        )

                    response = requests.post(
                        webhook_url,
                        params={"wait": "true"},
                        data={
                            "content": content,
                            "username": "Chest Scanner"
                        },
                        files=files if files else None,
                        timeout=30
                    )
                finally:
                    if image_file is not None:
                        image_file.close()

                if response.status_code not in (200, 204):
                    print(
                        f"[DISCORD] Ошибка сообщения {index + 1}/{paired_count} "
                        f"{response.status_code}: {response.text}"
                    )
                    return

                try:
                    message_data = response.json()
                    message_id = message_data.get("id")
                    if message_id:
                        sent_message_ids.append(message_id)
                except (ValueError, AttributeError):
                    pass

                time.sleep(0.7)

            self.save_discord_message_ids(sent_message_ids)

            print(
                f"[DISCORD] Отчёт отправлен: {len(sent_message_ids)} сообщений, "
                f"{len(table_image_paths)} таблиц."
            )

        except requests.RequestException as error:
            print(f"[DISCORD] Ошибка сети Discord: {error}")


# ============================================================
# ЗАПУСК
# ============================================================

def main():
    reader = ChestReader()

    print("Программа запущена.\n")
    print("Горячие клавиши:")
    print("  F6  — сохранить области распознавания цифр.")
    print("  F7  — показать рамки ячеек.")
    print("  F8  — принудительно считать сундук.")
    print("  F9  — отправить результаты в Discord.")
    print("  F10 — завершить программу.\n")

    scan_interval = reader.config["scan_interval_seconds"]
    last_scan_time = 0

    while True:
        try:
            if keyboard.is_pressed("f10"):
                print("Программа остановлена.")
                break

            if keyboard.is_pressed("f6"):
                reader.save_quantity_debug()
                time.sleep(0.5)

            if keyboard.is_pressed("f7"):
                reader.show_grid_debug()
                time.sleep(0.5)

            if keyboard.is_pressed("f8"):
                reader.scan_chest(force=True)
                time.sleep(0.5)

            if keyboard.is_pressed("f9"):
                reader.send_all_chests_to_discord()
                time.sleep(0.5)

#            current_time = time.time()
#
#            if current_time - last_scan_time >= scan_interval:
#                reader.scan_chest(force=False)
#                last_scan_time = current_time

            time.sleep(0.1)

        except KeyboardInterrupt:
            print("\nПрограмма остановлена.")
            break

        except Exception as error:
            print(f"\n[ОШИБКА] {type(error).__name__}: {error}")
            time.sleep(2)


if __name__ == "__main__":
    main()