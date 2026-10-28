"""ChestReader mixin: распознавание предметов и неизвестных."""
import hashlib
import os
import shutil
import uuid

import cv2

from chest_scanner.commons import absolute_path, save_json
from chest_scanner.image_utils import crop_relative, image_distance, image_distance_coarse, resize_image
from chest_scanner.items import next_image_path, normalize_items_db
from chest_scanner.logging_setup import logger
from chest_scanner import paths

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
        """Двухуровневое распознавание: грубое 16×16, затем точное 48×48."""
        COARSE_TOP_N = 10
        ih, iw = icon_image.shape[:2]

        # Уровень 1: грубое сравнение
        coarse = []
        for item in self.items_db["items"]:
            if not item.get("enabled", True):
                continue
            name = item.get("name")
            if not name:
                continue
            for path in (item.get("images") or [item.get("image")]):
                if not path:
                    continue
                tpl = self.template_cache.get_resized(path, iw, ih)
                if tpl is None:
                    continue
                cd = image_distance_coarse(icon_image, tpl)
                coarse.append({"name": name, "image": path, "coarse": cd,
                               "category": item.get("category", "uncategorized")})

        if not coarse:
            un = self.add_unknown_item(icon_image)
            return un, True, float("inf"), "uncategorized"

        coarse.sort(key=lambda c: c["coarse"])
        top = coarse[:COARSE_TOP_N]

        # Уровень 2: точное сравнение
        candidates = []
        for c in top:
            tpl = self.template_cache.get_resized(c["image"], iw, ih)
            if tpl is None:
                continue
            dist = image_distance(icon_image, tpl)
            candidates.append({"name": c["name"], "image": c["image"],
                               "distance": dist, "category": c["category"]})

        if not candidates:
            un = self.add_unknown_item(icon_image)
            return un, True, float("inf"), "uncategorized"

        best_per = {}
        for c in candidates:
            n = c["name"]
            if n not in best_per or c["distance"] < best_per[n]["distance"]:
                best_per[n] = c

        ordered = sorted(best_per.values(), key=lambda x: x["distance"])
        best = ordered[0]
        bn, bd, bc = best["name"], best["distance"], best.get("category", "uncategorized")
        sd = ordered[1]["distance"] if len(ordered) > 1 else float("inf")

        threshold = self.config.get("item_match_threshold", 1500)
        margin = self.config.get("item_match_margin", 200)

        if self.config.get("print_item_candidates", False):
            logger.debug(f"[ITEM] {bn} d={bd:.1f} second={sd:.1f} diff={sd - bd:.1f}")

        if bd > threshold:
            un = self.add_unknown_item(icon_image)
            logger.info(f"Слабое совпадение (SSIM). Создан: {un}")
            return un, True, bd, "uncategorized"

        if sd != float("inf") and (sd - bd) < margin:
            un = self.add_unknown_item(icon_image)
            logger.info(f"Неоднозначное совпадение. Создан: {un}")
            return un, True, bd, "uncategorized"

        if bn.startswith("unknown_"):
            rn = self.add_unknown_item(icon_image)
            return rn, rn.startswith("unknown_"), bd, bc

        return bn, False, bd, bc

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
        self._rebuild_stack_cache()
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
        best = self._compare_with_db(icon)
        if best is None:
            return {"row": slot["row"] + 1, "column": slot["column"] + 1,
                    "item": None, "quantity": 1, "unknown": True,
                    "similarity_score": 0.0, "category": "uncategorized",
                    "_icon": icon, "_slot_image": si}
        qty = self.read_quantity(si)
        return {"row": slot["row"] + 1, "column": slot["column"] + 1,
                "item": best["name"], "quantity": qty, "unknown": False,
                "similarity_score": round(best["distance"], 2),
                "category": best.get("category", "uncategorized"),
                "_icon": None, "_slot_image": None}

    def _compare_with_db(self, icon_image):
        best = None
        ih, iw = icon_image.shape[:2]
        for item in self.items_db["items"]:
            if not item.get("enabled", True):
                continue
            name = item.get("name")
            if not name:
                continue
            for path in (item.get("images") or [item.get("image")]):
                if not path:
                    continue
                tpl = self.template_cache.get_resized(path, iw, ih)
                if tpl is None:
                    continue
                dist = image_distance(icon_image, tpl)
                if best is None or dist < best["distance"]:
                    best = {"name": name, "distance": dist,
                            "category": item.get("category", "uncategorized")}
        return best

