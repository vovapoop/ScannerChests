"""ChestReader mixin: распознавание количества по цифрам."""
import os

import cv2
import numpy as np

from chest_scanner.logging_setup import logger
from chest_scanner import paths

class OcrMixin:
    """Methods extracted from the original ChestReader."""

    def load_digit_templates(self):
        templates = {}
        for d in range(10):
            p = os.path.join(paths.DIGITS_DIR, f"{d}.png")
            if not os.path.exists(p):
                continue
            img = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
            if img is None:
                continue
            prep = self.prepare_digit(img)
            if prep is not None:
                templates[str(d)] = prep
        logger.info(f"Загружено цифр: {len(templates)}/10")
        return templates

    @staticmethod
    def make_digit_mask(image):
        if image is None or image.size == 0:
            return None
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image.copy()
        if len(np.unique(gray)) <= 3:
            return cv2.inRange(gray, 100, 255)
        mask = cv2.inRange(gray, 250, 255)
        kernel = np.ones((2, 2), np.uint8)
        return cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)

    @staticmethod
    def prepare_digit(image):
        if image is None or image.size == 0:
            return None
        binary = OcrMixin.make_digit_mask(image)
        if binary is None:
            return None
        points = cv2.findNonZero(binary)
        if points is None:
            return None
        x, y, w, h = cv2.boundingRect(points)
        digit = binary[y:y + h, x:x + w]
        if digit.size == 0:
            return None
        cw, chh, pad = 24, 32, 2
        sc = min((cw - pad * 2) / max(1, w), (chh - pad * 2) / max(1, h))
        nw, nh = max(1, round(w * sc)), max(1, round(h * sc))
        digit = cv2.resize(digit, (nw, nh), interpolation=cv2.INTER_NEAREST)
        canvas = np.zeros((chh, cw), dtype=np.uint8)
        xo, yo = (cw - nw) // 2, (chh - nh) // 2
        canvas[yo:yo + nh, xo:xo + nw] = digit
        return canvas

    def match_digit(self, digit_image):
        if not self.digit_templates:
            return None, 0.0
        prep = self.prepare_digit(digit_image)
        if prep is None:
            return None, 0.0
        pm = prep > 0
        best_d, best_s = None, -1.0
        for digit, tpl in self.digit_templates.items():
            if tpl is None:
                continue
            tm = tpl > 0
            for sy in range(-3, 4):
                for sx in range(-3, 4):
                    tf = np.float32([[1, 0, sx], [0, 1, sy]])
                    shifted = cv2.warpAffine(tm.astype(np.uint8), tf,
                                             (tm.shape[1], tm.shape[0]),
                                             flags=cv2.INTER_NEAREST, borderValue=0).astype(bool)
                    inter = np.logical_and(pm, shifted).sum()
                    union = np.logical_or(pm, shifted).sum()
                    if union == 0:
                        continue
                    iou = inter / union
                    pa, pb = pm.sum(), shifted.sum()
                    ss = 1.0 - abs(pa - pb) / max(1, pa, pb)
                    score = iou * 0.8 + ss * 0.2
                    if score > best_s:
                        best_s = score
                        best_d = digit
        if best_s < 0.10:
            return None, best_s
        return best_d, best_s

    def read_quantity(self, slot_image):
        if not self.digit_templates:
            return 1
        ci = crop_relative(slot_image, self.config["count_roi_in_slot"])
        if ci is None or ci.size == 0:
            return 1
        binary = self.make_digit_mask(ci)
        if binary is None:
            return 1
        ih, iw = binary.shape[:2]
        lc, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
        chars = []
        for lb in range(1, lc):
            x, y, w, h, area = stats[lb]
            if area < 2 or h < 3:
                continue
            if w > iw * 0.8 or h > ih * 0.95:
                continue
            chars.append((x, y, x + w, y + h))
        if not chars:
            return 1
        chars.sort(key=lambda c: c[0])
        result = []
        for x1, y1, x2, y2 in chars:
            x1, y1 = max(0, x1 - 1), max(0, y1 - 1)
            x2, y2 = min(iw, x2 + 1), min(ih, y2 + 1)
            d, _ = self.match_digit(binary[y1:y2, x1:x2])
            if d is not None:
                result.append(d)
        txt = "".join(result)
        if not txt:
            return 1
        try:
            q = int(txt)
        except ValueError:
            return 1
        return q if 1 <= q <= 9999 else 1

