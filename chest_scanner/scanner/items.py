"""ChestReader mixin: распознавание предметов и неизвестных."""
import hashlib
import os
import shutil
import uuid

import cv2

from chest_scanner import paths
from chest_scanner.commons import absolute_path, save_json
from chest_scanner.image_utils import (
    crop_relative,
    hamming,
    image_distance,
    phash,
    preprocess_icon,
    resize_image,
)
from chest_scanner.items import next_image_path, normalize_items_db
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

    def extract_icon(self, slot_image):
        return crop_relative(slot_image, self.config["icon_roi_in_slot"])

    def detect_item(self, icon_image):
        """Двухуровневое распознавание: грубый отбор по pHash, затем точное 48×48.

        Возвращает (name, unknown, distance, category). При неопределённом
        результате name=None, unknown=True — создание записи в базе
        (unknown_item) выполняется вызывающей стороной (workflow).
        """
        if icon_image is None or icon_image.size == 0:
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
        if icon_image is None or icon_image.size == 0:
            return "unknown_empty"
        small = resize_image(icon_image, 32, 32)
        ih = hashlib.md5(small.tobytes()).hexdigest()[:10]
        un = f"unknown_{ih}"
        rel = f"unknown_items/{un}.png"
        fp = absolute_path(rel)
        existing = None
        for item in self.items_db.get("items", []):
            imgs = [str(p) for p in (item.get("images") or [item.get("image")]) if p]
            if item.get("name") == un or rel in imgs:
                existing = item
                break
        if existing and existing.get("name", "").strip() and not existing["name"].startswith("unknown_"):
            return existing["name"]
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        if not os.path.exists(fp):
            cv2.imwrite(fp, icon_image)
        if un in self.prompted_unknowns:
            return existing.get("name", un) if existing else un
        self.prompted_unknowns.add(un)
        self.unknown_item_category = "uncategorized"
        try:
            if callable(self.unknown_item_prompt):
                user_name = (self.unknown_item_prompt(un, fp) or "").strip()
            else:
                user_name = input(f"Название для {un}: ").strip()
        except Exception:
            user_name = ""
        fn = user_name or un
        cat = getattr(self, "unknown_item_category", "uncategorized")
        if existing is None:
            existing = {"id": str(uuid.uuid4()), "name": fn, "stack_size": 64,
                        "images": [rel], "image": rel, "aliases": [],
                        "enabled": True, "category": cat, "tags": []}
            self.items_db.setdefault("items", []).append(existing)
        else:
            existing["name"] = fn
            if cat != "uncategorized":
                existing["category"] = cat
            imgs = [str(x) for x in (existing.get("images") or [existing.get("image")]) if x]
            if rel not in imgs:
                imgs.append(rel)
            existing["images"] = imgs
            existing["image"] = imgs[0] if imgs else rel
        if fn != un:
            nr = next_image_path(fn, ".png")
            nf = absolute_path(nr)
            try:
                if os.path.abspath(fp) != os.path.abspath(nf):
                    shutil.move(fp, nf)
                    imgs = existing.get("images", [])
                    imgs = [nr if p == rel else p for p in imgs]
                    existing["images"] = imgs
                    existing["image"] = imgs[0] if imgs else nr
            except OSError as e:
                logger.error(f"Переименование изображения: {e}")
        self.items_db = normalize_items_db(self.items_db)
        save_json(paths.ITEMS_FILE, self.items_db)
        self._rebuild_indexes()
        logger.info(f"Предмет сохранён: {fn}")
        return fn

    def _process_slot(self, slot):
        si = slot["image"]
        if si is None or si.size == 0:
            return None
        if self.is_empty_slot(si):
            return None
        icon = self.extract_icon(si)
        if icon is None or icon.size == 0:
            return None
        name, unknown, dist, cat = self.detect_item(icon)
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

