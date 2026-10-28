"""ChestReader mixin: детекция открытого сундука и автогенерация шаблонов."""
import os

import cv2
import numpy as np

from chest_scanner.commons import absolute_path
from chest_scanner.logging_setup import logger
from chest_scanner.screenshot import screenshot_bgr, screenshot_region_bgr

class DetectionMixin:
    """Methods extracted from the original ChestReader."""

    def auto_generate_templates(self, force=False):
        screen = screenshot_bgr()
        sh, sw = screen.shape[:2]
        grid = self.get_grid_rect(screen)
        x, y, w, h = grid["x"], grid["y"], grid["width"], grid["height"]
        created = []

        chest_rel = self.config.get("chest_template", "assets/chest_open.png")
        chest_path = absolute_path(chest_rel)
        if chest_path and (force or not os.path.exists(chest_path)):
            hh = max(30, int(h * 0.20))
            ty1, ty2 = max(0, y - hh), min(sh, y + 4)
            tx1, tx2 = max(0, x - 4), min(sw, x + w + 4)
            if ty2 > ty1 and tx2 > tx1:
                tpl = screen[ty1:ty2, tx1:tx2]
                if tpl is not None and tpl.size > 0:
                    os.makedirs(os.path.dirname(chest_path), exist_ok=True)
                    cv2.imwrite(chest_path, tpl)
                    self.chest_template = tpl
                    created.append("chest")
                    logger.info(f"Создан шаблон сундука: {chest_rel}")

        empty_rel = self.config.get("empty_slot_template", "assets/empty_slot.png")
        empty_path = absolute_path(empty_rel)
        if empty_path and (force or not os.path.exists(empty_path)):
            slots = self.get_slots(screen)
            imgs = [s["image"] for s in slots if s["image"] is not None and s["image"].size > 0]
            if imgs:
                ref_shape = imgs[0].shape
                valid = []
                for img in imgs:
                    if img.shape == ref_shape:
                        valid.append(img)
                    else:
                        valid.append(cv2.resize(img, (ref_shape[1], ref_shape[0])))
                if valid:
                    stacked = np.stack(valid, axis=0)
                    median_slot = np.median(stacked, axis=0).astype(np.uint8)
                    os.makedirs(os.path.dirname(empty_path), exist_ok=True)
                    cv2.imwrite(empty_path, median_slot)
                    self.empty_slot_template = median_slot
                    created.append("empty_slot")
                    logger.info(f"Создан шаблон пустого слота: {empty_rel}")
        return created

    def chest_is_open(self, screen):
        if self.chest_template is None:
            return False
        sg = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
        tg = cv2.cvtColor(self.chest_template, cv2.COLOR_BGR2GRAY)
        th, tw = tg.shape[:2]
        sh, sw = sg.shape[:2]
        if th > sh or tw > sw:
            return False
        res = cv2.matchTemplate(sg, tg, cv2.TM_CCOEFF_NORMED)
        _, mx, _, _ = cv2.minMaxLoc(res)
        return mx >= self.config["chest_template_threshold"]

    def chest_is_open_fast(self):
        if self._last_chest_pos is None or self.chest_template is None:
            return self.chest_is_open(screenshot_bgr())
        pos = self._last_chest_pos
        m = 50
        x, y = max(0, pos["x"] - m), max(0, pos["y"] - m)
        w, h = pos["width"] + m * 2, pos["height"] + m * 2
        try:
            region = screenshot_region_bgr(x, y, w, h)
        except Exception:
            return self.chest_is_open(screenshot_bgr())
        tg = cv2.cvtColor(self.chest_template, cv2.COLOR_BGR2GRAY)
        rg = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        th, tw = tg.shape[:2]
        rh, rw = rg.shape[:2]
        if th > rh or tw > rw:
            return False
        res = cv2.matchTemplate(rg, tg, cv2.TM_CCOEFF_NORMED)
        _, mx, _, _ = cv2.minMaxLoc(res)
        return mx >= self.config["chest_template_threshold"]

