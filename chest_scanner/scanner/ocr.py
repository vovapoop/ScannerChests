"""ChestReader mixin: распознавание количества по цифрам."""
import os

import cv2
import numpy as np

from chest_scanner import paths
from chest_scanner.image_utils import crop_relative
from chest_scanner.logging_setup import logger


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
    def make_digit_mask(image, bin_thresh=250):
        if image is None or image.size == 0:
            return None
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image.copy()
        if len(np.unique(gray)) <= 3:
            return cv2.inRange(gray, 100, 255)
        mask = cv2.inRange(gray, bin_thresh, 255)
        kernel = np.ones((2, 2), np.uint8)
        return cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)

    def _find_digit_boxes(self, binary):
        """Возвращает боксы цифр (x1, y1, x2, y2) из бинарной маски.

        Компоненты шире типичного символа считаются слипшимися цифрами
        и разделяются по вертикальному профилю.
        """
        ih, iw = binary.shape[:2]
        lc, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
        boxes = []
        for lb in range(1, lc):
            x, y, w, h, area = stats[lb]
            if area < 2 or h < 3:
                continue
            if w > iw * 0.8 or h > ih * 0.95:
                continue
            boxes.append((x, y, w, h))
        if not boxes:
            return []
        boxes.sort(key=lambda b: (b[0], b[1]))
        median_w = float(np.median([b[2] for b in boxes]))
        out = []
        for x, y, w, h in boxes:
            parts = self._split_component(binary, x, y, w, h, median_w)
            for sx, sy, sw, sh in parts:
                out.append((sx, sy, sx + sw, sy + sh))
        out.sort(key=lambda b: (b[0], b[1]))
        return out

    @staticmethod
    def _split_component(binary, x, y, w, h, median_w):
        """Разделяет широкий компонент маски на отдельные цифры."""
        if w <= max(2, int(median_w * 1.6)):
            return [(x, y, w, h)]
        roi = binary[y:y + h, x:x + w]
        col_sum = (roi > 0).sum(axis=0)
        gaps = [i for i in range(1, w) if col_sum[i] == 0]
        if gaps:
            boxes, prev = [], 0
            for g in gaps:
                if g - prev >= 1:
                    boxes.append((x + prev, y, g - prev, h))
                prev = g
            if w - prev >= 1:
                boxes.append((x + prev, y, w - prev, h))
            return [b for b in boxes if b[2] >= 2]
        # Слипшиеся цифры без разрывов — делим на две равные части.
        hw = w // 2
        return [(x, y, hw, h), (x + hw, y, w - hw, h)]

    def _digit_char_boxes(self, ci):
        """Боксы цифр в области количества; при пустом результате — мягче порог."""
        binary = self.make_digit_mask(ci)
        if binary is None:
            return []
        boxes = self._find_digit_boxes(binary)
        if not boxes:
            binary = self.make_digit_mask(ci, 220)
            if binary is not None:
                boxes = self._find_digit_boxes(binary)
        return boxes

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
        chars = self._digit_char_boxes(ci)
        if not chars:
            return 1
        ih, iw = ci.shape[:2]
        result = []
        for x1, y1, x2, y2 in chars:
            x1, y1 = max(0, x1 - 1), max(0, y1 - 1)
            x2, y2 = min(iw, x2 + 1), min(ih, y2 + 1)
            d, _ = self.match_digit(ci[y1:y2, x1:x2])
            if d is not None:
                result.append(d)
        txt = "".join(result)
        if not txt:
            return 1
        if any(c not in "0123456789" for c in txt):
            return 1
        try:
            q = int(txt)
        except ValueError:
            return 1
        return q if 1 <= q <= 999999 else 1

