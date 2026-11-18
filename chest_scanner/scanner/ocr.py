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
            try:
                if not os.path.exists(p):
                    continue
                img = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
                if img is None:
                    logger.warning("OCR digit template %s is unreadable", d)
                    continue
                prep = self.prepare_digit(img)
            except (OSError, cv2.error, TypeError, ValueError) as exc:
                logger.warning("Skipping invalid OCR digit template %s: %s", d, exc)
                continue
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
        """Split a merged pair only when its width and projection support it."""
        # The digit templates are normalized from glyphs with a 120:168
        # width/height ratio. Use the component height as a stable reference;
        # median_w alone is unreliable when the ROI contains only one component.
        expected_w = h * (120.0 / 168.0)
        if w <= max(2, int(expected_w * 1.55)):
            return [(x, y, w, h)]

        roi = binary[y:y + h, x:x + w]
        col_sum = (roi > 0).sum(axis=0)
        if w < 2:
            return [(x, y, w, h)]

        # Look for a pronounced vertical valley, while requiring both resulting
        # pieces to remain close to a normal single-glyph width. This avoids
        # cutting naturally wide glyphs or splitting on weak random valleys.
        min_part = max(2, int(expected_w * 0.38))
        max_part = int(expected_w * 1.35)
        candidates = [
            i for i in range(min_part, w - min_part + 1)
            if col_sum[i] <= max(1, int(col_sum.max() * 0.55))
            and i <= max_part
            and (w - i) <= max_part
        ]
        if not candidates:
            return [(x, y, w, h)]

        split_at = min(candidates, key=lambda i: (col_sum[i], abs(i - w / 2)))
        parts = []
        for left, right in ((0, split_at), (split_at, w)):
            piece = roi[:, left:right]
            points = cv2.findNonZero(piece)
            if points is None:
                return [(x, y, w, h)]
            px, py, pw, ph = cv2.boundingRect(points)
            if pw < min_part or pw > max_part:
                return [(x, y, w, h)]
            parts.append((x + left + px, y + py, pw, ph))
        return parts

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
        candidate_scores = []
        for digit, tpl in self.digit_templates.items():
            if tpl is None:
                continue
            tm = tpl > 0
            best_for_digit = -1.0
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
                    size_similarity = 1.0 - abs(pa - pb) / max(1, pa, pb)
                    score = iou * 0.8 + size_similarity * 0.2
                    best_for_digit = max(best_for_digit, score)
            if best_for_digit >= 0:
                candidate_scores.append((best_for_digit, digit))

        if not candidate_scores:
            return None, 0.0
        candidate_scores.sort(reverse=True)
        best_s, best_d = candidate_scores[0]
        runner_up = candidate_scores[1][0] if len(candidate_scores) > 1 else 0.0

        # A high raw score is not enough when another digit matches almost
        # equally well. Abstain instead of silently choosing the wrong stack.
        if best_s < 0.50 or best_s - runner_up < 0.08:
            return None, best_s
        return best_d, best_s

    @staticmethod
    def _valid_ocr_image(image):
        """Reject malformed frames before passing them to OpenCV OCR routines."""
        if not isinstance(image, np.ndarray) or image.size == 0:
            return False
        if image.dtype != np.uint8 or image.ndim not in (2, 3):
            return False
        if image.ndim == 3 and image.shape[2] != 3:
            return False
        return bool(np.isfinite(image).all())

    def read_quantity(self, slot_image):
        """Determine stack quantity; None means the visible value is uncertain."""
        if not self.digit_templates or not self._valid_ocr_image(slot_image):
            return None
        roi = self.config.get("count_roi_in_slot")
        if (not isinstance(roi, (list, tuple)) or len(roi) != 4
                or not all(isinstance(v, (int, float, np.number)) and np.isfinite(v)
                           for v in roi)):
            return None
        x, y, w, h = map(float, roi)
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1 or y + h > 1:
            return None
        ci = crop_relative(slot_image, self.config["count_roi_in_slot"])
        if not self._valid_ocr_image(ci):
            return None
        chars = self._digit_char_boxes(ci)
        if not chars:
            # Minecraft omits the stack-count glyph when the stack contains
            # exactly one item. Only treat the ROI as an implicit 1 when it is
            # genuinely blank at both thresholds used by the digit detector.
            # Visible-but-unsegmented content remains unknown, never guessed.
            gray = cv2.cvtColor(ci, cv2.COLOR_BGR2GRAY) if len(ci.shape) == 3 else ci
            has_count_ink = bool(np.any(gray >= 220))
            if not has_count_ink:
                return 1
            return None

        ih, iw = ci.shape[:2]
        result = []
        for x1, y1, x2, y2 in chars:
            x1, y1 = max(0, x1 - 1), max(0, y1 - 1)
            x2, y2 = min(iw, x2 + 1), min(ih, y2 + 1)
            digit, score = self.match_digit(ci[y1:y2, x1:x2])
            # Не пропускаем нераспознанные символы: это могло бы превратить
            # "64" в ошибочное "4". Низкая оценка также считается неизвестной.
            if digit is None or digit not in "0123456789":
                return None
            if score < 0.50:
                return None
            result.append(digit)

        try:
            quantity = int("".join(result))
        except (TypeError, ValueError):
            return None
        return quantity if 1 <= quantity <= 999999 else None

