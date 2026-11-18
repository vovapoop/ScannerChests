"""ChestReader mixin: распознавание предметов и неизвестных."""
import hashlib
import os
import tempfile
from datetime import datetime

import cv2
import numpy as np

from chest_scanner import paths
from chest_scanner.commons import (
    absolute_path,
    load_json_recover,
    profile_data_guard,
    save_json,
)
from chest_scanner.image_utils import (
    crop_relative,
    hamming,
    image_distance,
    phash,
    preprocess_icon,
    resize_image,
)
from chest_scanner.logging_setup import logger


class ItemsMixin:
    """Methods extracted from the original ChestReader."""

    def is_empty_slot(self, slot_image):
        if self.empty_slot_template is None:
            return False
        if slot_image is None or slot_image.size == 0:
            return True
        tpl = resize_image(self.empty_slot_template, slot_image.shape[1], slot_image.shape[0])
        return image_distance(slot_image, tpl) < self.config["empty_slot_threshold"]

    @staticmethod
    def _valid_icon_image(image):
        """Accept only finite, non-empty BGR frames in OpenCV's byte format."""
        return (
            isinstance(image, np.ndarray)
            and image.dtype == np.uint8
            and image.ndim == 3
            and image.shape[2] == 3
            and image.shape[0] > 0
            and image.shape[1] > 0
            and bool(np.isfinite(image).all())
        )

    def extract_icon(self, slot_image):
        if not self._valid_icon_image(slot_image):
            return None
        roi = self.config.get("icon_roi_in_slot")
        if (not isinstance(roi, (list, tuple)) or len(roi) != 4
                or not all(isinstance(v, (int, float, np.number)) and np.isfinite(v)
                           for v in roi)):
            return None
        x, y, w, h = map(float, roi)
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1 or y + h > 1:
            return None
        icon = crop_relative(slot_image, roi)
        return icon if self._valid_icon_image(icon) else None

    def detect_item(self, icon_image):
        """Двухуровневое распознавание: грубый отбор по pHash, затем точное 48×48.

        Возвращает (name, unknown, distance, category). При неопределённом
        результате name=None, unknown=True — создание записи в базе
        (unknown_item) выполняется вызывающей стороной (workflow).
        """
        if not self._valid_icon_image(icon_image):
            return None, True, float("inf"), "uncategorized"
        prep = preprocess_icon(icon_image)
        ih, iw = icon_image.shape[:2]

        # Кэш результата по хэшу иконки (ускорение повторных сканов).
        # Кэшируются только уверенные распознавания.
        cache_key = None
        if self.config.get("item_detect_cache", True):
            small = resize_image(prep, 16, 16)
            if small is not None:
                cache_key = hashlib.md5(small.tobytes()).hexdigest()
                hit = self._detect_cache.get(cache_key)
                if hit is not None:
                    return hit

        def finish(result):
            if cache_key and result[0] is not None and not result[1]:
                if len(self._detect_cache) < 50_000:
                    self._detect_cache[cache_key] = result
            return result

        # Уровень 1: грубый отбор кандидатов по pHash-индексу
        ihash = phash(prep)
        thr = int(self.config.get("phash_threshold", self.DEFAULT_PHASH_THRESHOLD))
        candidates = [e for e in self._phash_entries if hamming(ihash, e["hash"]) <= thr]
        if not candidates and self._phash_entries:
            # Фоллбэк: ближайшие по Хэммингу шаблоны
            candidates = sorted(self._phash_entries,
                                key=lambda e: hamming(ihash, e["hash"]))[:10]

        if not candidates:
            return finish((None, True, float("inf"), "uncategorized"))

        # Уровень 2: точное сравнение
        best_per = {}
        for cand in candidates:
            tpl = self.template_cache.get_resized(cand["image"], iw, ih)
            if tpl is None:
                continue
            dist = image_distance(icon_image, tpl)
            n = cand["name"]
            if n not in best_per or dist < best_per[n]["distance"]:
                best_per[n] = {"name": n, "distance": dist,
                               "category": cand["category"]}

        if not best_per:
            return finish((None, True, float("inf"), "uncategorized"))

        ordered = sorted(best_per.values(), key=lambda x: x["distance"])
        best = ordered[0]
        bn, bd, bc = best["name"], best["distance"], best.get("category", "uncategorized")
        sd = ordered[1]["distance"] if len(ordered) > 1 else float("inf")

        threshold = self.config.get("item_match_threshold", 1500)
        margin = self.config.get("item_match_margin", 200)

        if self.config.get("print_item_candidates", False):
            logger.debug(f"[ITEM] {bn} d={bd:.1f} second={sd:.1f} diff={sd - bd:.1f}")

        if bd > threshold:
            logger.info("Слабое совпадение (SSIM). Будет создан unknown.")
            return finish((None, True, bd, "uncategorized"))
        if sd != float("inf") and (sd - bd) < margin:
            logger.info("Неоднозначное совпадение. Будет создан unknown.")
            return finish((None, True, bd, "uncategorized"))
        if bn.startswith("unknown_"):
            return finish((None, True, bd, bc))
        return finish((bn, False, bd, bc))

    def add_unknown_item(self, icon_image):
        """Stores an unknown icon in the persistent review queue.

        Scanning never opens a dialog; repeated icons increment the queue count.
        """
        if not self._valid_icon_image(icon_image):
            return "unknown_empty"
        small = resize_image(icon_image, 32, 32)
        ih = hashlib.md5(small.tobytes()).hexdigest()[:10]
        un = f"unknown_{ih}"
        rel = f"unknown_items/{un}.png"
        fp = absolute_path(rel)

        try:
            with profile_data_guard():
                os.makedirs(os.path.dirname(fp), exist_ok=True)
                if not os.path.isfile(fp) or cv2.imread(fp, cv2.IMREAD_COLOR) is None:
                    fd, temporary = tempfile.mkstemp(
                        prefix=f".{un}.", suffix=".png", dir=os.path.dirname(fp))
                    os.close(fd)
                    try:
                        if not cv2.imwrite(temporary, icon_image):
                            raise OSError(f"Не удалось сохранить изображение: {temporary}")
                        if cv2.imread(temporary, cv2.IMREAD_COLOR) is None:
                            raise OSError(f"Сохранённое изображение повреждено: {temporary}")
                        os.replace(temporary, fp)
                    finally:
                        try:
                            if os.path.exists(temporary):
                                os.remove(temporary)
                        except OSError:
                            pass

                queue = load_unknown_queue(paths.UNKNOWN_QUEUE_FILE)
                entries = queue.setdefault("items", [])
                entry = next((x for x in entries if x.get("id") == un), None)
                stamp = datetime.now().astimezone().isoformat(timespec="seconds")
                if entry is None:
                    entries.append({
                        "id": un, "image": rel, "occurrences": 1,
                        "first_seen": stamp, "last_seen": stamp,
                    })
                else:
                    entry["occurrences"] = int(entry.get("occurrences", 0)) + 1
                    entry["last_seen"] = stamp
                    entry["image"] = rel
                save_json(paths.UNKNOWN_QUEUE_FILE, queue, backup=True)
        except (OSError, cv2.error, ValueError, TypeError) as e:
            logger.error(f"\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0434\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u043d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u044b\u0439 \u043f\u0440\u0435\u0434\u043c\u0435\u0442 {un} \u0432 \u043e\u0447\u0435\u0440\u0435\u0434\u044c: {e}")
            self.prompted_unknowns.add(un)
            return un

        self.prompted_unknowns.add(un)
        return un


    def _process_slot(self, slot):
        """Process one slot while honoring cancellation between expensive steps."""
        if self._scan_cancel_event.is_set():
            return None
        si = slot["image"]
        if si is None or not getattr(si, "size", 0):
            return None
        if isinstance(si, np.ndarray) and not self._valid_icon_image(si):
            return None
        if self.is_empty_slot(si):
            return None
        if self._scan_cancel_event.is_set():
            return None
        icon = self.extract_icon(si)
        if icon is None or icon.size == 0:
            return None
        if self._scan_cancel_event.is_set():
            return None
        name, unknown, dist, cat = self.detect_item(icon)
        if self._scan_cancel_event.is_set():
            return None
        if unknown or name is None:
            return {"row": slot["row"] + 1, "column": slot["column"] + 1,
                    "item": None, "quantity": 1, "unknown": True,
                    "similarity_score": round(float(dist), 2),
                    "category": cat, "_icon": icon, "_slot_image": si}
        qty = self.read_quantity(si)
        return {"row": slot["row"] + 1, "column": slot["column"] + 1,
                "item": name, "quantity": qty, "unknown": False,
                "similarity_score": round(float(dist), 2),
                "category": cat, "_icon": None, "_slot_image": None}
def load_unknown_queue(path):
    """Load the queue, recovering its validated backup or preserving bad data."""
    return load_json_recover(
        path, {"items": []},
        lambda value: isinstance(value, dict)
        and isinstance(value.get("items"), list)
        and all(isinstance(entry, dict) for entry in value["items"]),
        allow_empty=True)
