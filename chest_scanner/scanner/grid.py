"""ChestReader mixin: сетка, слоты, калибровка и отладка."""
import glob
import os
import time

import cv2
import numpy as np

from chest_scanner import paths
from chest_scanner.commons import save_json
from chest_scanner.image_utils import crop_relative
from chest_scanner.logging_setup import logger, now_moscow
from chest_scanner.screenshot import screenshot_bgr


class GridMixin:
    """Methods extracted from the original ChestReader."""

    def get_grid_rect(self, screen, grid=None):
        """Прямоугольник сетки; `grid` позволяет подменить координаты
        (используется живым предпросмотром настроек без мутации self.grid)."""
        g = grid or self.grid
        sh, sw = screen.shape[:2]
        sx, sy = sw / self.ref_w, sh / self.ref_h
        x1 = round(g["x1"] * sx)
        y1 = round(g["y1"] * sy)
        x2 = round(g["x2"] * sx)
        y2 = round(g["y2"] * sy)
        if x2 < x1:
            x1, x2 = x2, x1
        if y2 < y1:
            y1, y2 = y2, y1
        return {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1,
                "columns": g["columns"], "rows": g["rows"]}

    def get_slots(self, screen, grid=None):
        grid = self.get_grid_rect(screen, grid)
        sw = grid["width"] / grid["columns"]
        sh = grid["height"] / grid["rows"]
        slots = []
        for row in range(grid["rows"]):
            for col in range(grid["columns"]):
                x1 = round(grid["x"] + col * sw) + 1
                x2 = round(grid["x"] + (col + 1) * sw) - 1
                y1 = round(grid["y"] + row * sh) + 1
                y2 = round(grid["y"] + (row + 1) * sh) - 1
                slots.append({"row": row, "column": col,
                              "image": screen[y1:y2, x1:x2],
                              "x1": x1, "y1": y1, "x2": x2, "y2": y2})
        return slots

    # ── Автоопределение границ сундука ──

    #: Допустимые диапазоны для запасного автоопределения columns/rows.
    AUTO_COLUMNS_RANGE = (2, 18)
    AUTO_ROWS_RANGE = (1, 12)

    #: Повторы автоопределения: временное перекрытие (тултип предмета,
    #: анимация открытия сундука) не должно срывать калибровку.
    AUTO_DETECT_ATTEMPTS = 3
    AUTO_DETECT_RETRY_DELAY = 0.25

    #: Сколько диагностических снимков неудачного автоопределения хранить.
    GRID_FAILURE_KEEP = 5

    def detect_grid_rect(self, screen):
        """Ищет прямоугольник сетки сундука на скриншоте.

        Возвращает {"x", "y", "width", "height", "columns", "rows"} или None.
        Сначала пробует подтвердить пресет из self.grid (columns/rows),
        при несовпадении определяет количество ячеек по проекциям.
        """
        try:
            return self._detect_grid_rect_impl(screen)
        except Exception as e:
            logger.error(f"Автоопределение сетки: {e}", exc_info=True)
            return None

    def _detect_grid_rect_impl(self, screen):
        sh, sw = screen.shape[:2]
        if sw < 200 or sh < 200:
            return None
        vlines, hlines = self._grid_line_masks(screen)
        candidates = self._grid_candidates(vlines, hlines)
        if not candidates:
            logger.debug("Сетка: линии сетки не найдены")
            return None
        preset_cols = int(self.grid.get("columns", 9))
        preset_rows = int(self.grid.get("rows", 6))
        verified = []
        for (x, y, w, h) in candidates:
            info = self._verify_grid_candidate(vlines, hlines, x, y, w, h)
            if info is not None:
                verified.append((x, y, w, h, info))
        if not verified:
            logger.debug(f"Сетка: кандидатов {len(candidates)}, ни один не "
                         f"прошёл проверку проекциями")
            return None
        # Кандидат, над которым действительно виден заголовок сундука,
        # надёжнее пресета: при перекрытии части сетки (тултип предмета)
        # иначе можно принять за сундук инвентарь игрока ниже.
        titled = [c for c in verified if self._candidate_has_chest_title(screen, c[4])]
        if titled:
            verified = titled
        elif getattr(self, "chest_template", None) is not None:
            # Шаблон есть, но ни над одним кандидатом нет заголовка сундука.
            # Это либо чужой интерфейс (инвентарь игрока, другой контейнер),
            # либо шаблон устарел: он снят при другом GUI Scale и в текущем
            # размере интерфейса заголовок уже не совпадает. Во втором случае
            # запрет блокировал бы калибровку навсегда (после смены GUI Scale
            # или разрешения шаблон не совпадает никогда), поэтому сверяем
            # найденные сетки с сохранённой: масштаб и положение. Если ни
            # одна не совпала по обоим признакам, сохранённые координаты
            # устарели и шаблон ничего не доказывает — сетку принимаем по
            # геометрии, шаблон будет переснят сразу после калибровки.
            configured = self._configured_grid_rect(screen)
            aligned = [c for c in verified
                       if self._overlap_ratio(c[:4], configured) >= 0.5]
            same_scale = [c for c in verified
                          if abs(self.scale_from_grid_width(c[4]["width"]) - 1.0) <= 0.02]
            if aligned and same_scale:
                logger.debug("Сетка: над кандидатами нет заголовка сундука")
                return None
            scale = self.scale_from_grid_width(verified[0][4]["width"])
            logger.warning(
                f"Сетка: шаблон сундука устарел (масштаб интерфейса "
                f"{scale:.2f}) — сетка принята по геометрии, "
                f"шаблон будет переснят")
            self._chest_match_scale = scale
        # Приоритет: кандидаты, совпавшие с пресетом (±1 ячейка), среди
        # них — самый верхний на экране (GUI сундука выше инвентаря игрока).
        preset = [c for c in verified
                  if abs(c[4]["columns"] - preset_cols) <= 1
                  and abs(c[4]["rows"] - preset_rows) <= 1]
        pool = preset if preset else verified
        _, _, _, _, info = min(pool, key=lambda c: c[1])
        logger.debug(f"Сетка найдена: ({info['x']},{info['y']}) "
                     f"{info['width']}x{info['height']}, "
                     f"{info['columns']}x{info['rows']} ячеек "
                     f"(кандидатов: {len(verified)}, с пресетом: {len(preset)})")
        return {"x": info["x"], "y": info["y"],
                "width": info["width"], "height": info["height"],
                "columns": info["columns"], "rows": info["rows"]}

    def _configured_grid_rect(self, screen):
        """Настроенная сетка в координатах кадра (или None).

        Нужна, чтобы отличить устаревший шаблон от чужого интерфейса:
        если найденная сетка стоит не там, где настроенная, сохранённые
        координаты точно устарели — шаблон тогда ничего не доказывает.
        """
        try:
            return self.get_grid_rect(screen)
        except Exception:
            return None

    @staticmethod
    def _overlap_ratio(rect, other):
        """Доля площади rect, попавшая в other (1.0, если other нет)."""
        if other is None:
            return 1.0
        x, y, w, h = rect
        ix = max(0, min(x + w, other["x"] + other["width"]) - max(x, other["x"]))
        iy = max(0, min(y + h, other["y"] + other["height"]) - max(y, other["y"]))
        area = float(w * h)
        return (ix * iy) / area if area > 0 else 0.0

    def _grid_line_masks(self, screen):
        """Маски вертикальных и горизонтальных линий сетки.

        Тёмные тонкие линии на светлом фоне GUI выделяются через blackhat,
        затем длинными морфологическими ядрами остаются только линии.
        """
        gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
        dark = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, k)
        _, binm = cv2.threshold(dark, 32, 255, cv2.THRESH_BINARY)
        sh, sw = binm.shape[:2]
        kv = max(30, sh // 24)
        kh = max(30, sw // 40)
        join_v = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 7))
        join_h = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 1))
        vlines = cv2.morphologyEx(
            cv2.morphologyEx(binm, cv2.MORPH_CLOSE, join_v),
            cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, kv)))
        hlines = cv2.morphologyEx(
            cv2.morphologyEx(binm, cv2.MORPH_CLOSE, join_h),
            cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (kh, 1)))
        return vlines, hlines

    def _grid_candidates(self, vlines, hlines):
        """Кандидаты-прямоугольники: замкнутые контуры на объединённой маске."""
        sh, sw = vlines.shape[:2]
        mask = cv2.bitwise_or(vlines, hlines)
        closed = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)),
                            iterations=2)
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        area_screen = float(sw * sh)
        out = []
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            if w < 100 or h < 50:
                continue
            area = float(w * h)
            if area < 0.005 * area_screen or area > 0.6 * area_screen:
                continue
            # Прямоугольность: заполненные ячейки делают края контура
            # неровными, поэтому порог мягкий — точную проверку делает
            # верификация проекциями.
            if cv2.contourArea(c) < 0.5 * area:
                continue
            out.append((int(x), int(y), int(w), int(h)))
        return out

    @staticmethod
    def _line_peaks(mask, axis, min_len):
        """Позиции линий в кандидате: проекция маски по оси и группировка.

        axis=0 — вертикальные линии (профиль по столбцам),
        axis=1 — горизонтальные линии (профиль по строкам).
        """
        proj = (mask > 0).sum(axis=axis)
        if proj.max() <= 0:
            return []
        thr = max(min_len, int(proj.max() * 0.5))
        idx = np.where(proj >= thr)[0]
        groups = []
        for i in idx:
            if groups and i - groups[-1][-1] <= 2:
                groups[-1].append(int(i))
            else:
                groups.append([int(i)])
        return [float(np.mean(g)) for g in groups]

    @staticmethod
    def _peaks_regular(peaks):
        """Шаг между линиями равномерный (std/mean < 0.2)."""
        if len(peaks) < 2:
            return False
        diffs = np.diff(peaks)
        mean = float(np.mean(diffs))
        return mean > 10 and float(np.std(diffs)) / mean < 0.2

    def _verify_grid_candidate(self, vlines, hlines, x, y, w, h):
        """Верификация кандидата проекциями; уточняет прямоугольник по пикам.

        Возвращает {"x","y","width","height","columns","rows"} в локальных
        координатах кандидата или None.
        """
        vsub = vlines[y:y + h, x:x + w]
        hsub = hlines[y:y + h, x:x + w]
        vp = self._line_peaks(vsub, axis=0, min_len=max(10, int(h * 0.5)))
        hp = self._line_peaks(hsub, axis=1, min_len=max(10, int(w * 0.5)))
        if len(vp) < 2 or len(hp) < 2:
            return None
        # На мелком интерфейсе часть линий может потеряться, а рядом появиться
        # лишние пики (границы панели, края предметов). Оставляем самый длинный
        # равномерный ряд линий.
        vp = self._regular_subset(vp)
        hp = self._regular_subset(hp)
        if len(vp) < 2 or len(hp) < 2:
            return None
        if not self._peaks_regular(vp) or not self._peaks_regular(hp):
            return None
        # Крайние линии сетки часто сливаются с рамкой контейнера и не
        # попадают в маску — достраиваем их по регулярному шагу.
        vp = self._extend_peaks(vp, 0.0, float(w))
        hp = self._extend_peaks(hp, 0.0, float(h))
        cols = len(vp) - 1
        rows = len(hp) - 1
        lo, hi = self.AUTO_COLUMNS_RANGE
        if not (lo <= cols <= hi):
            return None
        lo, hi = self.AUTO_ROWS_RANGE
        if not (lo <= rows <= hi):
            return None
        cw = (vp[-1] - vp[0]) / cols
        chh = (hp[-1] - hp[0]) / rows
        if cw < 12 or chh < 12:
            return None
        if not 0.6 <= cw / chh <= 1.6:  # ячейки примерно квадратные
            return None
        # Границы сетки — центры крайних линий (как в ручной калибровке).
        x1 = int(round(x + vp[0]))
        x2 = int(round(x + vp[-1]))
        y1 = int(round(y + hp[0]))
        y2 = int(round(y + hp[-1]))
        return {"x": max(0, x1), "y": max(0, y1),
                "width": x2 - x1, "height": y2 - y1,
                "columns": cols, "rows": rows}

    def _candidate_has_chest_title(self, screen, info):
        """Проверяет, что над найденной сеткой есть заголовок сундука.

        Использует шаблон сундука и ту же полосу допуска, что и
        chest_is_open(), но относительно координат кандидата. Если шаблона
        или матчера нет (например, в тестах), проверка не выполняется и
        возвращается True.
        """
        tpl = getattr(self, "chest_template", None)
        matcher = getattr(self, "_match_template_gray", None)
        band_ok = getattr(self, "_match_in_title_band", None)
        if tpl is None or matcher is None or band_ok is None:
            return True
        # Размер интерфейса сундука может отличаться (GUI Scale), поэтому
        # масштаб шаблона оцениваем по ширине найденной сетки.
        scale = self.scale_from_grid_width(info["width"])
        if abs(scale - 1.0) > 1e-3:
            tpl = cv2.resize(tpl, (max(1, int(round(tpl.shape[1] * scale))),
                                   max(1, int(round(tpl.shape[0] * scale)))),
                             interpolation=cv2.INTER_AREA)
        th, tw = tpl.shape[:2]
        band = self._title_band(info["x"], info["y"],
                                 info["width"], info["height"])
        m = 40
        x = max(0, band[0] - m)
        y = max(0, band[1] - m)
        w = min(screen.shape[1] - x, (band[2] - band[0]) + tw + m * 2)
        h = min(screen.shape[0] - y, (band[3] - band[1]) + th + m * 2)
        if w < tw or h < th:
            return False
        region = screen[y:y + h, x:x + w]
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        match = matcher(gray, cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY))
        if match is None:
            return False
        match = (match[0], match[1] + x, match[2] + y)
        ok = band_ok(match, band,
                     self.config.get("chest_template_threshold", 0.88),
                     f"grid_candidate (scale={scale:.2f})")
        if ok:
            # Запоминаем масштаб: дальше chest_is_open() и fast-проверка
            # ищут шаблон сразу в нужном размере.
            self._chest_match_scale = scale
        return ok

    @staticmethod
    def _extend_peaks(peaks, lo, hi):
        """Дополняет пики виртуальными крайними линиями по регулярному шагу.

        Линия добавляется, только если полностью помещается в границы
        кандидата (с допуском 2 px), иначе сетка считается завершённой.
        """
        if len(peaks) < 2:
            return peaks
        cell = float(np.mean(np.diff(peaks)))
        out = list(peaks)
        guard = 0
        while out[0] - cell >= lo - 2 and guard < 32:
            out.insert(0, out[0] - cell)
            guard += 1
        guard = 0
        while out[-1] + cell <= hi + 2 and guard < 32:
            out.append(out[-1] + cell)
            guard += 1
        return out


    @staticmethod
    def _regular_subset(peaks, tol=0.25):
        """Самый длинный равномерный ряд пиков (шаг ± tol от медианного).

        Пропущенная линия или лишний пик (рамка панели, край предмета)
        ломали бы весь кандидат — здесь они отбрасываются.
        """
        if len(peaks) < 3:
            return peaks
        step = float(np.median(np.diff(peaks)))
        if step <= 0:
            return peaks
        best = []
        for start in range(len(peaks) - 1):
            chain = [peaks[start]]
            for nxt in peaks[start + 1:]:
                diff = nxt - chain[-1]
                if abs(diff - step) <= step * tol:
                    chain.append(nxt)
                elif diff > step * (1 + tol):
                    break
            if len(chain) > len(best):
                best = chain
        return best

    def auto_detect_grid(self):
        """Однократное автоопределение сетки открытого сундука.

        Ищет сетку на скриншоте, пересчитывает координаты в
        reference_screen_size и сохраняет их в config.json. Возвращает
        словарь chest_grid или None при неудаче.
        """
        rect = None
        screen = None
        for attempt in range(self.AUTO_DETECT_ATTEMPTS):
            screen = screenshot_bgr()
            # Снимок храним для отладочного окна: при неудаче GUI покажет
            # ровно тот кадр, на котором не нашлась сетка.
            self._last_auto_grid_screen = screen
            if self.chest_template is not None and not self.chest_is_open(screen):
                # Не прерываемся: размер интерфейса мог измениться (GUI Scale),
                # тогда старый шаблон не совпадает, но сетку всё равно можно
                # найти и подтвердить заголовком уже в новом масштабе.
                logger.warning("Автоопределение: шаблон сундука не совпал при "
                               "текущем размере интерфейса — продолжаем поиск "
                               "сетки")
            rect = self.detect_grid_rect(screen)
            if rect is not None:
                break
            logger.debug(f"Автоопределение: попытка {attempt + 1}/"
                         f"{self.AUTO_DETECT_ATTEMPTS} не нашла сетку")
            if attempt + 1 < self.AUTO_DETECT_ATTEMPTS:
                time.sleep(self.AUTO_DETECT_RETRY_DELAY)
        if rect is None:
            self._save_grid_failure(screen, "not_found")
            logger.warning("Автоопределение: сетка сундука не найдена. "
                           "Убедитесь, что интерфейс сундука открыт и виден целиком.")
            return None
        sh, sw = screen.shape[:2]
        scx, scy = sw / self.ref_w, sh / self.ref_h
        self.grid = {"x1": round(rect["x"] / scx), "y1": round(rect["y"] / scy),
                     "x2": round((rect["x"] + rect["width"]) / scx),
                     "y2": round((rect["y"] + rect["height"]) / scy),
                     "columns": int(rect["columns"]), "rows": int(rect["rows"])}
        self.config["chest_grid"] = self.grid
        save_json(paths.CONFIG_FILE, self.config)
        scale = getattr(self, "_chest_match_scale", None) or 1.0
        old_scale = float(self.config.get("chest_template_scale", 1.0) or 1.0)
        # Если размер интерфейса отличается от того, при котором снят шаблон,
        # старый шаблон больше не подходит — переснимаем его вместе с сеткой.
        stale_templates = abs(scale - old_scale) > 0.02
        logger.info(f"Автоопределение сетки: ({self.grid['x1']},{self.grid['y1']})"
                    f" -> ({self.grid['x2']},{self.grid['y2']}), "
                    f"{self.grid['columns']}x{self.grid['rows']} ячеек, "
                    f"масштаб интерфейса {scale:.2f}")
        try:
            created = self.auto_generate_templates(force=stale_templates)
            if created:
                logger.info(f"Автоопределение: созданы шаблоны {', '.join(created)}")
        except Exception as e:
            logger.error(f"Автоопределение: генерация шаблонов: {e}", exc_info=True)
        if stale_templates:
            # Шаблоны пересняты в текущем масштабе — дальше ищем 1:1.
            self._chest_match_scale = 1.0
        self.config["chest_template_scale"] = 1.0
        save_json(paths.CONFIG_FILE, self.config)
        return dict(self.grid)

    def _save_grid_failure(self, screen, reason="not_found"):
        """Сохраняет снимок неудачного автоопределения для диагностики.

        Хранится не больше GRID_FAILURE_KEEP последних файлов, чтобы
        папка не разрасталась.
        """
        try:
            d = os.path.join(paths.RESULTS_DIR, "grid_debug")
            os.makedirs(d, exist_ok=True)
            if screen is None or getattr(screen, "size", 0) == 0:
                return None
            ts = now_moscow().strftime("%Y%m%d_%H%M%S")
            p = os.path.join(d, f"auto_grid_fail_{reason}_{ts}.png")
            if not cv2.imwrite(p, screen):
                return None
            logger.warning(f"Автоопределение: снимок экрана сохранён "
                           f"для диагностики: {p}")
            keep = max(1, int(self.GRID_FAILURE_KEEP))
            files = sorted(glob.glob(os.path.join(d, "auto_grid_fail_*.png")))
            for old in files[:-keep]:
                try:
                    os.remove(old)
                except OSError:
                    pass
            return p
        except Exception as e:
            logger.error(f"Автоопределение: не удалось сохранить снимок: {e}",
                         exc_info=True)
            return None

    def show_grid_debug(self, screen=None):
        """Показывает отладочное окно по снимку экрана.

        screen передаётся, когда снимок уже сделан в нужный момент
        (например, пока окно программы скрыто). Если не передан, снимок
        делается здесь — тогда в кадр попадёт всё, что есть на экране.
        """
        if self._grid_debug_active:
            return
        self._grid_debug_active = True
        try:
            if not isinstance(screen, np.ndarray):
                if screen is not None:
                    logger.warning("Grid debug: ???????? ???? ndarray, ??????? %s; "
                                   "????? ?????? ????? ??????", type(screen).__name__)
                screen = screenshot_bgr()
            if screen is None or screen.size == 0 or screen.ndim < 2:
                logger.warning("Grid debug: ?????? ?????? ??????????")
                return
            sh, sw = screen.shape[:2]
            grid = self.get_grid_rect(screen)
            base = screen.copy()
            cv2.rectangle(base, (grid["x"], grid["y"]),
                          (grid["x"] + grid["width"], grid["y"] + grid["height"]),
                          (0, 0, 255), 2)
            for idx, slot in enumerate(self.get_slots(screen), 1):
                cv2.rectangle(base, (slot["x1"] - 1, slot["y1"] - 1),
                              (slot["x2"] + 1, slot["y2"] + 1), (0, 255, 0), 1)
                cv2.putText(base, str(idx), (slot["x1"] + 3, slot["y1"] + 13),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 0, 255), 1, cv2.LINE_AA)
            sx, sy = sw / self.ref_w, sh / self.ref_h
            for i, line in enumerate([
                f"Screen: {sw}x{sh}", f"Ref: {self.ref_w}x{self.ref_h}",
                f"Scale: {sx:.2f}x{sy:.2f}",
                f"TL: ({grid['x']},{grid['y']})",
                f"BR: ({grid['x'] + grid['width']},{grid['y'] + grid['height']})",
                f"Size: {grid['width']}x{grid['height']}",
                f"Slots: {grid['columns']}x{grid['rows']}"]):
                cv2.putText(base, line, (20, 40 + i * 25),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1, cv2.LINE_AA)
            cv2.circle(base, (grid["x"], grid["y"]), 6, (0, 255, 0), -1)
            cv2.circle(base, (grid["x"] + grid["width"], grid["y"] + grid["height"]), 6, (255, 100, 0), -1)
            win = "Chest grid debug"
            cv2.namedWindow(win, cv2.WINDOW_NORMAL)
            cursor = {"x": None, "y": None}

            def on_mouse(ev, x, y, fl, p):
                cursor["x"], cursor["y"] = x, y

            cv2.setMouseCallback(win, on_mouse)
            while True:
                frame = base.copy()
                cx, cy = cursor["x"], cursor["y"]
                if cx is not None and cy is not None:
                    cv2.line(frame, (cx - 15, cy), (cx + 15, cy), (0, 255, 255), 1)
                    cv2.line(frame, (cx, cy - 15), (cx, cy + 15), (0, 255, 255), 1)
                    tx, ty = min(cx + 12, sw - 180), max(cy - 10, 25)
                    cv2.putText(frame, f"({cx},{cy})", (int(tx), int(ty)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
                    if 0 <= cy < sh and 0 <= cx < sw:
                        b, g, r = screen[cy, cx]
                        cv2.putText(frame, f"BGR:{b},{g},{r}", (int(tx), int(ty) + 20),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
                cv2.putText(frame, "Esc/Q/Space - close", (20, sh - 12),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)
                cv2.imshow(win, frame)
                key = cv2.waitKey(30) & 0xFF
                if key in (27, ord("q"), ord(" ")):
                    break
            cv2.destroyWindow(win)
        except Exception as e:
            logger.error(f"Grid debug: {e}", exc_info=True)
        finally:
            self._grid_debug_active = False

    def calibrate_grid_interactive(self):
        screen = screenshot_bgr()
        sh, sw = screen.shape[:2]
        columns = self.grid.get("columns", 9)
        rows = self.grid.get("rows", 6)
        state = {"p1": None, "p2": None, "phase": "first"}
        win = "Chest Grid Calibration"
        cv2.namedWindow(win, cv2.WINDOW_NORMAL)

        def draw_preview(img, x1, y1, x2, y2):
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), 2)
            w, h = x2 - x1, y2 - y1
            if w <= 0 or h <= 0:
                return
            cw, ch = w / columns, h / rows
            for c in range(1, columns):
                px = int(x1 + c * cw)
                cv2.line(img, (px, y1), (px, y2), (0, 200, 0), 1)
            for r in range(1, rows):
                py = int(y1 + r * ch)
                cv2.line(img, (x1, py), (x2, py), (0, 200, 0), 1)
            cv2.circle(img, (x1, y1), 5, (0, 255, 0), -1)
            cv2.circle(img, (x2, y2), 5, (255, 100, 0), -1)

        def on_mouse(ev, x, y, fl, p):
            if ev != cv2.EVENT_LBUTTONDOWN:
                return
            if state["phase"] == "first":
                state["p1"] = (x, y)
                state["phase"] = "second"
            elif state["phase"] == "second":
                state["p2"] = (x, y)
                state["phase"] = "adjust"

        cv2.setMouseCallback(win, on_mouse)

        while True:
            frame = screen.copy()
            if state["phase"] == "first":
                cv2.putText(frame, "Click TOP-LEFT corner of grid", (20, 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2, cv2.LINE_AA)
                cv2.putText(frame, "Esc = cancel", (20, 72),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)
            elif state["phase"] == "second":
                p1 = state["p1"]
                cv2.circle(frame, p1, 5, (0, 255, 0), -1)
                cv2.putText(frame, f"TL: {p1}", (p1[0] + 10, p1[1] - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)
                cv2.putText(frame, "Click BOTTOM-RIGHT corner", (20, 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2, cv2.LINE_AA)
                cv2.putText(frame, "R = reset | Esc = cancel", (20, 72),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)
            else:
                p1, p2 = state["p1"], state["p2"]
                x1, y1 = min(p1[0], p2[0]), min(p1[1], p2[1])
                x2, y2 = max(p1[0], p2[0]), max(p1[1], p2[1])
                draw_preview(frame, x1, y1, x2, y2)
                w, h = x2 - x1, y2 - y1
                for i, line in enumerate([
                    f"Grid: ({x1},{y1}) -> ({x2},{y2})",
                    f"Size: {w}x{h} | Cell: {w / columns:.1f}x{h / rows:.1f}",
                    f"Slots: {columns}x{rows}"]):
                    cv2.putText(frame, line, (20, 40 + i * 25),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1, cv2.LINE_AA)
                cv2.putText(frame, "Arrows=move | WASD=TL | IJKL=BR", (20, sh - 50),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)
                cv2.putText(frame, "Enter=save | R=reset | Esc=cancel", (20, sh - 24),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)
            cv2.imshow(win, frame)
            key = cv2.waitKey(30) & 0xFF
            if key == 27:
                cv2.destroyWindow(win)
                return None
            if key == ord("r"):
                state.update({"p1": None, "p2": None, "phase": "first"})
                continue
            if state["phase"] != "adjust":
                continue
            p1, p2 = list(state["p1"]), list(state["p2"])
            if key in (13, ord("\r")):
                x1, y1 = min(p1[0], p2[0]), min(p1[1], p2[1])
                x2, y2 = max(p1[0], p2[0]), max(p1[1], p2[1])
                scx, scy = sw / self.ref_w, sh / self.ref_h
                self.grid = {"x1": round(x1 / scx), "y1": round(y1 / scy),
                             "x2": round(x2 / scx), "y2": round(y2 / scy),
                             "columns": columns, "rows": rows}
                self.config["chest_grid"] = self.grid
                save_json(paths.CONFIG_FILE, self.config)
                cv2.destroyWindow(win)
                created = self.auto_generate_templates(force=False)
                if created:
                    logger.info(f"Калибровка: созданы шаблоны {', '.join(created)}")
                return self.grid
            if key == 82:    p1[1] -= 1; p2[1] -= 1
            elif key == 84:  p1[1] += 1; p2[1] += 1
            elif key == 81:  p1[0] -= 1; p2[0] -= 1
            elif key == 83:  p1[0] += 1; p2[0] += 1
            elif key == ord("w"): p1[1] -= 1
            elif key == ord("s"): p1[1] += 1
            elif key == ord("a"): p1[0] -= 1
            elif key == ord("d"): p1[0] += 1
            elif key == ord("i"): p2[1] -= 1
            elif key == ord("k"): p2[1] += 1
            elif key == ord("j"): p2[0] -= 1
            elif key == ord("l"): p2[0] += 1
            state["p1"], state["p2"] = tuple(p1), tuple(p2)

    def save_quantity_debug(self):
        screen = screenshot_bgr()
        dd = os.path.join(paths.RESULTS_DIR, "quantity_debug")
        os.makedirs(dd, exist_ok=True)
        for slot in self.get_slots(screen):
            si = slot["image"]
            if si is None or si.size == 0:
                continue
            ci = crop_relative(si, self.config["count_roi_in_slot"])
            if ci is None or ci.size == 0:
                continue
            binary = self.make_digit_mask(ci)
            r, c = slot["row"] + 1, slot["column"] + 1
            for sfx, img in (("original", ci), ("binary", binary)):
                lg = cv2.resize(img, None, fx=15, fy=15, interpolation=cv2.INTER_NEAREST)
                cv2.imwrite(os.path.join(dd, f"slot_r{r}_c{c}_{sfx}.png"), lg)
        logger.debug(f"Области количества: {dd}")

    # ── Проверка настроек областей иконки и цифр ──

    #: Сколько заполненных слотов показывать в отчёте проверки.
    ROI_CHECK_SLOTS = 4

    def diagnose_roi_settings(self, screen=None, icon_roi=None, count_roi=None,
                              grid=None):
        """Проверка настроек областей иконки и цифр без полного скана.

        Возвращает {"overlay", "slots", "issues"}:
          overlay — снимок области сетки; белая рамка — слот, зелёная —
                    область иконки, жёлтая — область цифр;
          slots   — до ROI_CHECK_SLOTS заполненных слотов с обрезками
                    иконки/цифр, бинарной маской и распознанным числом;
          issues  — список замечаний (последний элемент — итог).

        `icon_roi`/`count_roi` позволяют проверить значения прямо из полей
        настроек до их сохранения в конфиг.
        """
        issues = []
        if icon_roi is None:
            icon_roi = self.config.get("icon_roi_in_slot")
        if count_roi is None:
            count_roi = self.config.get("count_roi_in_slot")

        def _check_roi(roi, title):
            if not isinstance(roi, (list, tuple)) or len(roi) != 4:
                _problem(f"{title}: нужно 4 числа (X, Y, Ширина, Высота).")
                return False
            x, y, w, h = roi
            if min(x, y, w, h) < 0:
                _problem(f"{title}: отрицательные значения недопустимы.")
                return False
            if w <= 0 or h <= 0:
                _problem(f"{title}: ширина и высота должны быть > 0.")
                return False
            if x + w > 1.0001 or y + h > 1.0001:
                _problem(
                    f"{title}: X+Ширина или Y+Высота выходят за пределы слота (0–1).")
                return False
            return True

        problems = 0

        def _problem(text):
            nonlocal problems
            problems += 1
            issues.append(text)

        rois_ok = _check_roi(icon_roi, "Область иконки")
        rois_ok = _check_roi(count_roi, "Область цифр") and rois_ok
        digit_templates_missing = not self.digit_templates
        if digit_templates_missing:
            issues.append("Шаблоны цифр не загружены (папка assets/digits) — "
                          "количество распознаваться не будет.")

        if screen is None:
            screen = screenshot_bgr()
        grid_rect = self.get_grid_rect(screen, grid)
        overlay = None
        slots_out = []
        filled = 0
        if rois_ok:
            # `grid` — переопределение в стиле конфига (x1..rows) либо None.
            for slot in self.get_slots(screen, grid):
                si = slot["image"]
                if si is None or si.size == 0 or self._slot_looks_empty(si):
                    continue
                filled += 1
                r, c = slot["row"] + 1, slot["column"] + 1
                icon = crop_relative(si, icon_roi)
                count = crop_relative(si, count_roi)
                if icon is None or icon.size == 0:
                    _problem(f"Слот r{r} c{c}: обрезка иконки пустая.")
                binary = self.make_digit_mask(count) if count is not None else None
                if binary is None or not np.count_nonzero(binary):
                    _problem(f"Слот r{r} c{c}: в области цифр нет светлых "
                             "пикселей — цифра обрезана или область неверна.")
                slots_out.append({
                    "row": r, "column": c, "icon": icon, "count": count,
                    "binary": binary,
                    "quantity": self.read_quantity(si)})
                if overlay is None:
                    overlay = self._roi_overlay(screen, grid_rect, slot,
                                                icon_roi, count_roi)
                if len(slots_out) >= self.ROI_CHECK_SLOTS:
                    break
        if filled == 0:
            _problem("Заполненных слотов не найдено — откройте сундук "
                     "с предметами и повторите проверку.")
        if problems == 0:
            if digit_templates_missing:
                issues.append("Области заданы корректно; для распознавания "
                              "количества добавьте шаблоны цифр.")
            else:
                issues.append("Проблем не обнаружено: области заданы корректно.")
        return {"overlay": overlay, "slots": slots_out, "issues": issues}

    def _slot_looks_empty(self, si):
        """Заполненность слота: шаблон пустого слота либо эвристика по яркости."""
        if getattr(self, "empty_slot_template", None) is not None \
                and hasattr(self, "is_empty_slot"):
            return self.is_empty_slot(si)
        gray = cv2.cvtColor(si, cv2.COLOR_BGR2GRAY)
        # Пустой слот тёмный и однородный; ярких пикселей почти нет.
        bright = int(np.count_nonzero(gray > 40))
        return bright < max(1, gray.size // 40)

    def _roi_overlay(self, screen, grid, slot, icon_roi, count_roi):
        """Кадр сетки с рамками областей иконки/цифр в указанном слоте."""
        x, y = grid["x"], grid["y"]
        area = screen[y:y + grid["height"], x:x + grid["width"]].copy()
        sx1, sy1 = slot["x1"] - x, slot["y1"] - y
        sx2, sy2 = slot["x2"] - x, slot["y2"] - y
        sw, sh = sx2 - sx1, sy2 - sy1
        if sw <= 0 or sh <= 0:
            return None

        def _rect(roi):
            return (sx1 + int(roi[0] * sw), sy1 + int(roi[1] * sh),
                    sx1 + int((roi[0] + roi[2]) * sw),
                    sy1 + int((roi[1] + roi[3]) * sh))

        cv2.rectangle(area, (sx1, sy1), (sx2, sy2), (255, 255, 255), 1)
        cv2.rectangle(area, _rect(icon_roi)[:2], _rect(icon_roi)[2:], (0, 255, 0), 2)
        cv2.rectangle(area, _rect(count_roi)[:2], _rect(count_roi)[2:], (0, 220, 255), 2)
        return area

