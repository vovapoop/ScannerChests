"""ChestReader mixin: детекция открытого сундука и автогенерация шаблонов."""
import os
import tempfile

import cv2
import numpy as np

from chest_scanner.commons import absolute_path
from chest_scanner.logging_setup import logger
from chest_scanner.screenshot import screenshot_bgr, screenshot_region_bgr


class DetectionMixin:
    """Methods extracted from the original ChestReader."""

    # Размер интерфейса сундука зависит от GUI Scale в игре и от разрешения:
    # шаблон, снятый при одном масштабе, при другом даёт score ниже порога
    # (0.85–0.87 на реальных скриншотах), поэтому шаблон масштабируется под
    # размер найденной сетки (см. scale_from_grid_width).

    def auto_generate_templates(self, force=False):
        guard = getattr(self, "_scanner_state_guard", None)
        if guard is None:
            return self._auto_generate_templates_locked(force=force)
        with guard(blocking=False) as acquired:
            if not acquired:
                logger.warning("Template generation skipped: scanner is busy")
                return []
            return self._auto_generate_templates_locked(force=force)

    def _auto_generate_templates_locked(self, force=False):
        screen = screenshot_bgr()
        sh, sw = screen.shape[:2]
        grid = self.get_grid_rect(screen)
        x, y, w, h = grid["x"], grid["y"], grid["width"], grid["height"]

        # Prepare every candidate before touching either destination or the
        # in-memory templates. A failed second write must not leave a mixed pair.
        candidates = []
        chest_rel = self.config.get("chest_template", "assets/chest_open.png")
        chest_path = absolute_path(chest_rel)
        if chest_path and (force or not os.path.exists(chest_path)):
            hh = max(30, int(h * 0.20))
            ty1, ty2 = max(0, y - hh), min(sh, y + 4)
            tx1, tx2 = max(0, x - 4), min(sw, x + w + 4)
            if ty2 > ty1 and tx2 > tx1:
                tpl = screen[ty1:ty2, tx1:tx2]
                if tpl is not None and tpl.size > 0:
                    candidates.append(("chest", chest_rel, chest_path, tpl.copy()))

        empty_rel = self.config.get("empty_slot_template", "assets/empty_slot.png")
        empty_path = absolute_path(empty_rel)
        if empty_path and (force or not os.path.exists(empty_path)):
            slots = self.get_slots(screen)
            imgs = [s["image"] for s in slots
                    if s["image"] is not None and s["image"].size > 0]
            if imgs:
                ref_shape = imgs[0].shape
                valid = [img if img.shape == ref_shape else
                         cv2.resize(img, (ref_shape[1], ref_shape[0]))
                         for img in imgs]
                median_slot = np.median(np.stack(valid, axis=0), axis=0).astype(np.uint8)
                candidates.append(("empty_slot", empty_rel, empty_path, median_slot))

        if not candidates:
            return []
        destinations = [os.path.normcase(os.path.abspath(item[2]))
                        for item in candidates]
        if len(destinations) != len(set(destinations)):
            raise ValueError("Chest and empty-slot templates must use different paths")

        staged = []
        published = []
        originals = {}
        try:
            for name, rel, target, image in candidates:
                directory = os.path.dirname(target) or "."
                os.makedirs(directory, exist_ok=True)
                fd, temp_path = tempfile.mkstemp(
                    prefix=f".{os.path.basename(target)}.",
                    suffix=os.path.splitext(target)[1] or ".png", dir=directory)
                os.close(fd)
                staged.append((name, rel, target, temp_path, image))
                if not cv2.imwrite(temp_path, image):
                    raise OSError(f"OpenCV could not write template: {target}")
                if os.path.exists(target):
                    with open(target, "rb") as stream:
                        originals[target] = stream.read()
                else:
                    originals[target] = None

            for name, rel, target, temp_path, image in staged:
                os.replace(temp_path, target)
                published.append(target)
        except Exception:
            # Restore any destination already replaced. If rollback itself
            # fails, retain the staged/original evidence and report loudly.
            for target in reversed(published):
                try:
                    original = originals[target]
                    if original is None:
                        os.remove(target)
                    else:
                        directory = os.path.dirname(target) or "."
                        fd, rollback_path = tempfile.mkstemp(
                            prefix=f".{os.path.basename(target)}.rollback.",
                            suffix=os.path.splitext(target)[1] or ".png", dir=directory)
                        try:
                            with os.fdopen(fd, "wb") as stream:
                                stream.write(original)
                                stream.flush()
                                os.fsync(stream.fileno())
                            os.replace(rollback_path, target)
                        finally:
                            if os.path.exists(rollback_path):
                                os.remove(rollback_path)
                except Exception:
                    logger.exception("Template rollback failed for %s", target)
            raise
        finally:
            for _name, _rel, _target, temp_path, _image in staged:
                try:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                except OSError:
                    logger.warning("Could not remove staged template %s", temp_path)

        for name, rel, _target, _temp_path, image in staged:
            if name == "chest":
                self.chest_template = image
            else:
                self.empty_slot_template = image
            logger.info("Created template: %s", rel)
        return [name for name, _rel, _target, _temp_path, _image in staged]

    def _title_band(self, x, y, w, h):
        """Полоса допустимого положения левого верхнего угла шаблона сундука.

        Шаблон снимается с полосы непосредственно над сеткой (см.
        auto_generate_templates), поэтому при открытом сундуке матч
        обязан лежать в узкой зоне над настроенной сеткой. Проверка зоны
        отсекает ложные срабатывания на других интерфейсах (инвентарь
        игрока, другие контейнеры) и тёмных участках мира.
        """
        pad_x = max(20, int(w * 0.06))
        pad_y = max(12, int(h * 0.08))
        up = max(pad_y, int(h * 0.45)) + pad_y
        return (x - pad_x, y - up, x + pad_x, y + pad_y)

    def _match_template_gray(self, gray, tpl_gray):
        """Матчинг шаблона; возвращает (score, x, y) или None, если шаблон
        не помещается в изображение."""
        th, tw = tpl_gray.shape[:2]
        sh, sw = gray.shape[:2]
        if th > sh or tw > sw:
            return None
        res = cv2.matchTemplate(gray, tpl_gray, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        return float(mx), int(loc[0]), int(loc[1])

    def _match_in_title_band(self, match, band, threshold, source):
        """Финальное решение по матчу: порог + попадание в зону сетки."""
        if match is None:
            return False
        score, mx_, my_ = match
        logger.debug(f"{source}: score={score:.3f} at ({mx_},{my_}), "
                     f"band={band}, threshold={threshold:.2f}")
        if score < threshold:
            return False
        if not (band[0] <= mx_ <= band[2] and band[1] <= my_ <= band[3]):
            logger.debug(f"{source}: похожий интерфейс вне зоны сетки сундука "
                         f"(score={score:.3f} at {mx_},{my_}) — считаем закрытым")
            return False
        return True

    @staticmethod
    def _template_at_scale(tpl_gray, scale):
        """Шаблон, приведённый к нужному масштабу интерфейса."""
        if abs(scale - 1.0) < 1e-3:
            return tpl_gray
        size = (max(1, int(round(tpl_gray.shape[1] * scale))),
                max(1, int(round(tpl_gray.shape[0] * scale))))
        return cv2.resize(tpl_gray, size, interpolation=cv2.INTER_AREA)

    def scale_from_grid_width(self, width):
        """Масштаб интерфейса по ширине найденной сетки.

        Настроенная сетка снята при каком-то размере интерфейса. Если
        найденная сетка шире или уже настроенной, значит, изменился
        GUI Scale в игре (или разрешение) — шаблон тоже нужно масштабировать.
        """
        base = float(self.grid.get("x2", 0) or 0) - float(self.grid.get("x1", 0) or 0)
        if base <= 0 or not width:
            return getattr(self, "_chest_match_scale", None) or 1.0
        return min(2.5, max(0.4, float(width) / base))

    def chest_is_open(self, screen):
        """Открыт ли сундук: шаблон в нужном масштабе найден в зоне сетки."""
        if self.chest_template is None:
            return False
        scale = getattr(self, "_chest_match_scale", None) or 1.0
        sg = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
        tg = cv2.cvtColor(self.chest_template, cv2.COLOR_BGR2GRAY)
        match = self._match_template_gray(sg, self._template_at_scale(tg, scale))
        grid = self.get_grid_rect(screen)
        band = self._title_band(grid["x"], grid["y"],
                                 grid["width"], grid["height"])
        return self._match_in_title_band(
            match, band, self.config["chest_template_threshold"],
            f"chest_is_open (scale={scale:.2f})")

    def chest_is_open_fast(self):
        """Быстрая проверка по прошлой позиции сундука."""
        if self.chest_template is None:
            return False
        if self._last_chest_pos is None:
            return self.chest_is_open(screenshot_bgr())
        pos = self._last_chest_pos
        scale = getattr(self, "_chest_match_scale", None) or 1.0
        band = self._title_band(pos["x"], pos["y"],
                                 pos["width"], pos["height"])
        tg = cv2.cvtColor(self.chest_template, cv2.COLOR_BGR2GRAY)
        tg = self._template_at_scale(tg, scale)
        th_, tw_ = tg.shape[:2]
        # Захватываем зону вокруг полосы заголовка с запасом так, чтобы
        # любой матч с левым верхним углом внутри полосы помещался целиком.
        m = 40
        x = max(0, band[0] - m)
        y = max(0, band[1] - m)
        w = (band[2] - band[0]) + tw_ + m * 2
        h = (band[3] - band[1]) + th_ + m * 2
        try:
            region = screenshot_region_bgr(x, y, w, h)
        except Exception:
            return self.chest_is_open(screenshot_bgr())
        rg = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        match = self._match_template_gray(rg, tg)
        if match is not None:
            # Переводим координаты матча в абсолютные экранные.
            match = (match[0], match[1] + x, match[2] + y)
        return self._match_in_title_band(
            match, band, self.config["chest_template_threshold"],
            "chest_is_open_fast")

