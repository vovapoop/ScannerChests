"""ChestReader mixin: сканирование сундука и автоскан."""
import glob
import hashlib
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from chest_scanner import paths, storage
from chest_scanner.commons import load_json, save_json
from chest_scanner.image_utils import resize_image
from chest_scanner.logging_setup import logger, now_moscow
from chest_scanner.screenshot import screenshot_bgr


class WorkflowMixin:
    """Methods extracted from the original ChestReader."""

    #: Максимальный размер results/chests.json до автоархивации в history.
    MAX_RESULTS_FILE_BYTES = 2 * 1024 * 1024

    def _maybe_archive_results(self):
        """Переносит результаты из chests.json в history при превышении лимита."""
        if not os.path.exists(paths.RESULTS_FILE):
            return False
        try:
            if os.path.getsize(paths.RESULTS_FILE) < self.MAX_RESULTS_FILE_BYTES:
                return False
        except OSError:
            return False
        data = load_json(paths.RESULTS_FILE, {"chests": []})
        chests = data.get("chests", []) if isinstance(data, dict) else []
        if not chests:
            return False
        ts = now_moscow().strftime("%Y-%m-%d_%H-%M-%S")
        hp = os.path.join(paths.HISTORY_DIR, f"report_{ts}.json")
        c = 2
        while os.path.exists(hp):
            hp = os.path.join(paths.HISTORY_DIR, f"report_{ts}_{c}.json")
            c += 1
        save_json(hp, data)
        save_json(paths.RESULTS_FILE, {"chests": []})
        logger.info(f"История архивирована: {os.path.basename(hp)}")
        return True

    def save_chest_result(self, cd):
        self._maybe_archive_results()
        data = load_json(paths.RESULTS_FILE, {"chests": []})
        if not isinstance(data, dict):
            data = {"chests": []}
        data.setdefault("chests", []).append(cd)
        save_json(paths.RESULTS_FILE, data)
        # Параллельная копия в SQLite для аналитики и резерва.
        try:
            storage.add_chest(paths.RESULTS_DB, cd)
        except Exception as e:
            logger.error(f"SQLite-запись результата: {e}")

    # ── Отмена последнего сканирования ──

    def undo_last_scan(self):
        """Удаляет последний результат из истории и статистики.

        Чистит SQLite-копию, chests.json, а если запись уже уехала в архив
        results/history — то и там. Возвращает удалённый скан (dict) либо
        None, если отменять нечего.
        """
        try:
            deleted = storage.delete_last(paths.RESULTS_DB)
        except Exception as e:
            logger.error(f"Отмена скана: SQLite: {e}")
            deleted = None
        last_id = (deleted or {}).get("id")
        data = load_json(paths.RESULTS_FILE, {"chests": []})
        if not isinstance(data, dict):
            data = {"chests": []}
        chests = data.setdefault("chests", [])
        removed = None
        if last_id:
            idx = next((i for i, c in enumerate(chests)
                        if isinstance(c, dict) and c.get("id") == last_id), None)
            if idx is not None:
                removed = chests.pop(idx)
                save_json(paths.RESULTS_FILE, data)
            else:
                removed = self._remove_from_newest_archive(last_id)
        elif chests:
            # База пуста (потерялась синхронизация) — откатываем по JSON.
            removed = chests.pop()
            save_json(paths.RESULTS_FILE, data)
        removed = removed or deleted
        if removed:
            logger.info(f"Скан отменён: {removed.get('timestamp', '?')}, "
                        f"слотов: {removed.get('occupied_slots', '?')}")
        else:
            logger.info("Отмена скана: результатов нет.")
        return removed

    def _remove_from_newest_archive(self, chest_id):
        """Удаляет скан из самого нового архива history (фолбэк отката)."""
        archives = sorted(
            glob.glob(os.path.join(paths.HISTORY_DIR, "report_*.json")),
            reverse=True)
        for path in archives:
            data = load_json(path, {"chests": []})
            chests = data.get("chests", []) if isinstance(data, dict) else []
            for i, c in enumerate(chests):
                if isinstance(c, dict) and c.get("id") == chest_id:
                    chests.pop(i)
                    save_json(path, data)
                    return c
        return None

    def scan_chest(self, force=False):
        if not self._scan_lock.acquire(blocking=False):
            return None
        try:
            self.reset_scan_cancel()
            # Причина, по которой скан не дал результата: None | "no_template"
            # | "chest_closed" | "no_changes" | "cancelled". Используется GUI
            # для понятных сообщений пользователю.
            self.last_scan_reason = None
            self.play_scan_start_sound()
            time.sleep(0.15)
            screen = screenshot_bgr()
            if self.chest_template is None:
                self.last_scan_reason = "no_template"
                return None
            if not self.chest_is_open(screen):
                self.last_scan_reason = "chest_closed"
                if self.chest_was_open:
                    self.chest_was_open = False
                    self.last_chest_hash = None
                    self._last_chest_pos = None
                return None
            self.chest_was_open = True
            grid = self.get_grid_rect(screen)
            self._last_chest_pos = {"x": grid["x"], "y": grid["y"],
                                     "width": grid["width"], "height": grid["height"]}
            chest_img = screen[grid["y"]:grid["y"] + grid["height"],
                               grid["x"]:grid["x"] + grid["width"]]
            small = resize_image(chest_img, 100, 60)
            ch = hashlib.md5(small.tobytes()).hexdigest()
            if not force and ch == self.last_chest_hash:
                self.last_scan_reason = "no_changes"
                return None
            self.last_chest_hash = ch

            slots = self.get_slots(screen)
            slot_list, totals, unknowns, categories = [], {}, set(), {}

            # Параллельная обработка слотов
            max_workers = min(8, max(1, len(slots)))
            results = []
            done = 0
            total_slots = len(slots)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(self._process_slot, s): s for s in slots}
                for future in as_completed(futures):
                    if self._scan_cancel_event.is_set():
                        self._scan_cancelled = True
                        self.last_scan_reason = "cancelled"
                        logger.info("Скан отменён пользователем")
                        executor.shutdown(wait=False, cancel_futures=True)
                        return None
                    try:
                        r = future.result()
                        if r is not None:
                            results.append(r)
                    except Exception as e:
                        logger.error(f"Ошибка обработки слота: {e}", exc_info=True)
                    done += 1
                    if self.scan_progress_cb is not None:
                        try:
                            self.scan_progress_cb(done, total_slots)
                        except Exception as e:
                            logger.error(f"Ошибка колбэка прогресса: {e}", exc_info=True)

            results.sort(key=lambda r: (r["row"], r["column"]))

            # Последовательная обработка неизвестных
            for r in results:
                if r["unknown"] and r["item"] is None:
                    icon = r.get("_icon")
                    name = self.add_unknown_item(icon) if icon is not None else "unknown_empty"
                    r["item"] = name
                    r["unknown"] = name.startswith("unknown_")
                    if r.get("_slot_image") is not None:
                        r["quantity"] = self.read_quantity(r["_slot_image"])

                name = r["item"]
                if name is None:
                    continue
                qty = r["quantity"]
                cat = r.get("category", "uncategorized")

                if r["unknown"]:
                    unknowns.add(name)

                slot_list.append({
                    "row": r["row"], "column": r["column"],
                    "item": name, "quantity": qty,
                    "unknown": r["unknown"], "category": cat,
                    "similarity_score": r["similarity_score"]})

                totals[name] = totals.get(name, 0) + qty
                categories.setdefault(cat, {})
                categories[cat][name] = categories[cat].get(name, 0) + qty

                r.pop("_icon", None)
                r.pop("_slot_image", None)

            cd = {
                "id": str(uuid.uuid4()),
                "timestamp": now_moscow().strftime("%d.%m.%Y %H:%M:%S"),
                "screen_resolution": [screen.shape[1], screen.shape[0]],
                "occupied_slots": len(slot_list),
                "unique_items": len(totals),
                "unknown_items": sorted(unknowns),
                "slots": slot_list,
                "totals": totals,
                "categories": categories,
            }
            self.save_chest_result(cd)
            self.play_success_sound()
            logger.info(f"Сундук считан: {len(slot_list)} ячеек, {len(totals)} уникальных")
            return cd
        finally:
            self._scan_lock.release()

    @property
    def auto_scan_active(self):
        return self._auto_scan_active

    def start_auto_scan(self):
        if self._auto_scan_active:
            return
        previous = self._auto_scan_thread
        if previous is not None and previous.is_alive():
            logger.warning("Автоскан ещё завершает предыдущую операцию; повторный запуск пропущен")
            return
        self._auto_scan_active = True
        self._auto_scan_thread = threading.Thread(target=self._auto_scan_loop, daemon=True)
        self._auto_scan_thread.start()
        logger.info("Автоскан запущен")

    def stop_auto_scan(self, timeout=5.0):
        self._auto_scan_active = False
        t = self._auto_scan_thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=max(0.0, float(timeout)))
        if t is None or not t.is_alive():
            self._auto_scan_thread = None
            logger.info("Автоскан остановлен")
        else:
            # Не теряем ссылку: повторный запуск не должен создавать второй
            # цикл, пока предыдущий ещё завершает текущую операцию.
            logger.warning("Автоскан ещё завершает текущую операцию")

    def _auto_scan_loop(self):
        while self._auto_scan_active:
            interval = max(0.5, float(self.config.get("scan_interval_seconds", 2)))
            try:
                if self.chest_is_open_fast():
                    result = self.scan_chest(force=False)
                    if result:
                        logger.info(f"Автоскан: {result['occupied_slots']} ячеек")
                elif self.chest_was_open:
                    self.chest_was_open = False
                    self.last_chest_hash = None
                    self._last_chest_pos = None
            except Exception as e:
                logger.error(f"Автоскан ошибка: {e}", exc_info=True)
            waited = 0.0
            while self._auto_scan_active and waited < interval:
                time.sleep(0.2)
                waited += 0.2

