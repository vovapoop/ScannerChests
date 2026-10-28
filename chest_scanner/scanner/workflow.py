"""ChestReader mixin: сканирование сундука и автоскан."""
import hashlib
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from chest_scanner.commons import load_json, save_json
from chest_scanner.image_utils import resize_image
from chest_scanner.logging_setup import logger, now_moscow
from chest_scanner import paths
from chest_scanner.screenshot import screenshot_bgr

class WorkflowMixin:
    """Methods extracted from the original ChestReader."""

    def save_chest_result(self, cd):
        data = load_json(paths.RESULTS_FILE, {"chests": []})
        if not isinstance(data, dict):
            data = {"chests": []}
        data.setdefault("chests", []).append(cd)
        save_json(paths.RESULTS_FILE, data)

    def scan_chest(self, force=False):
        if not self._scan_lock.acquire(blocking=False):
            return None
        try:
            self.play_scan_start_sound()
            time.sleep(0.15)
            screen = screenshot_bgr()
            if not self.chest_is_open(screen):
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
                return None
            self.last_chest_hash = ch

            slots = self.get_slots(screen)
            slot_list, totals, unknowns, categories = [], {}, set(), {}

            # Параллельная обработка слотов
            max_workers = min(8, max(1, len(slots)))
            results = []
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(self._process_slot, s): s for s in slots}
                for future in as_completed(futures):
                    try:
                        r = future.result()
                        if r is not None:
                            results.append(r)
                    except Exception as e:
                        logger.error(f"Ошибка обработки слота: {e}", exc_info=True)

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
        self._auto_scan_active = True
        self._auto_scan_thread = threading.Thread(target=self._auto_scan_loop, daemon=True)
        self._auto_scan_thread.start()
        logger.info("Автоскан запущен")

    def stop_auto_scan(self):
        self._auto_scan_active = False
        t = self._auto_scan_thread
        if t and t.is_alive():
            t.join(timeout=5)
        self._auto_scan_thread = None
        logger.info("Автоскан остановлен")

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

