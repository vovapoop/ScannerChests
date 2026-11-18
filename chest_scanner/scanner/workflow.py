"""ChestReader mixin: сканирование сундука и автоскан."""
import glob
import hashlib
import json
import math
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from chest_scanner import paths, storage
from chest_scanner.commons import results_history_guard, save_json
from chest_scanner.history_utils import history_sort_key
from chest_scanner.image_utils import resize_image
from chest_scanner.logging_setup import logger, now_moscow
from chest_scanner.screenshot import screenshot_bgr

_RESULTS_LOCK_PATH = paths.RESULTS_FILE


class WorkflowMixin:
    """Methods extracted from the original ChestReader."""

    #: Максимальный размер results/chests.json до автоархивации в history.
    MAX_RESULTS_FILE_BYTES = 2 * 1024 * 1024

    def _maybe_archive_results(self):
        """????????? ????????? ?????????? ? history ??? ?????????? ??????.

        ?????????? True ??? ?????????, False ???? ??? ?? ????? ? None ???
        ??????. ????????? ?????? ????? ???? ????? ???????????? ? ????????
        ?????????? ??? ????????? ?????, ? ?? ??????? ??? ????????.
        """
        if not os.path.exists(paths.RESULTS_FILE):
            return False
        try:
            if os.path.getsize(paths.RESULTS_FILE) < self.MAX_RESULTS_FILE_BYTES:
                return False
            with open(paths.RESULTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError, UnicodeError) as e:
            logger.error(f"????????????? ????????: ?? ??????? ????????? ???????: {e}")
            return None

        if not isinstance(data, dict) or not isinstance(data.get("chests"), list):
            logger.error("????????????? ????????: ???????????? ????????? chests.json")
            return None
        chests = data["chests"]
        if not chests:
            return False
        if not all(isinstance(chest, dict) for chest in chests):
            logger.error("????????????? ????????: ?????? ???????????? ???????? ???????????? ??????")
            return None

        # ????? ?????????? ?????????? ????? ??? ???? ???????, ? ????????
        # ???? ??? ?? ??????. ???? ?????? ?????, ????? ?? ??????? ??????.
        archive_path = None
        try:
            archives = glob.glob(os.path.join(paths.HISTORY_DIR, "report_*.json"))
            for candidate in archives:
                try:
                    with open(candidate, "r", encoding="utf-8") as f:
                        archived = json.load(f)
                    if isinstance(archived, dict) and archived.get("chests") == chests:
                        archive_path = candidate
                        break
                except (json.JSONDecodeError, OSError, UnicodeError):
                    continue

            if archive_path is None:
                ts = now_moscow().strftime("%Y-%m-%d_%H-%M-%S")
                archive_path = os.path.join(paths.HISTORY_DIR, f"report_{ts}.json")
                c = 2
                while os.path.exists(archive_path):
                    archive_path = os.path.join(paths.HISTORY_DIR, f"report_{ts}_{c}.json")
                    c += 1
                save_json(archive_path, data)

            # ???????? ???? ????????? ?????? ????? ?????????????? ?????? ??????.
            save_json(paths.RESULTS_FILE, {"chests": []})
        except (OSError, TypeError, ValueError) as e:
            logger.error(f"????????????? ?? ?????????; ???????? ??????? ?????????: {e}")
            return None

        logger.info(f"??????? ????????????: {os.path.basename(archive_path)}")
        return True

    def save_chest_result(self, cd):
        """Serialize history mutations across scan workers and GUI actions."""
        with results_history_guard(_RESULTS_LOCK_PATH):
            return self._save_chest_result_locked(cd)

    @staticmethod
    def _find_existing_scan(record_id, current_chests):
        """Find an already committed scan by ID in current JSON or its archives."""
        if record_id is None:
            return None
        key = str(record_id)
        for record in current_chests:
            if isinstance(record, dict) and record.get("id") is not None:
                if str(record["id"]) == key:
                    return record

        for archive_path in sorted(glob.glob(
                os.path.join(paths.HISTORY_DIR, "report_*.json"))):
            try:
                with open(archive_path, "r", encoding="utf-8") as stream:
                    archived = json.load(stream)
            except (OSError, json.JSONDecodeError, UnicodeError) as exc:
                raise ValueError(
                    f"Unreadable history archive; duplicate check is unsafe: {archive_path}") from exc
            if not isinstance(archived, dict) or not isinstance(
                    archived.get("chests"), list):
                raise ValueError(
                    f"Invalid history archive structure; duplicate check is unsafe: {archive_path}")
            for record in archived["chests"]:
                if not isinstance(record, dict):
                    raise ValueError(
                        f"Invalid history record; duplicate check is unsafe: {archive_path}")
                if isinstance(record, dict) and record.get("id") is not None:
                    if str(record["id"]) == key:
                        return record
        return None

    def _save_chest_result_locked(self, cd):
        if self._maybe_archive_results() is None:
            return False
        if os.path.exists(paths.RESULTS_FILE):
            try:
                with open(paths.RESULTS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError, UnicodeError) as e:
                logger.error(f"\u0421\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0438\u0435 \u0441\u043a\u0430\u043d\u0430 \u043e\u0442\u043c\u0435\u043d\u0435\u043d\u043e: \u043d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043f\u0440\u043e\u0447\u0438\u0442\u0430\u0442\u044c {paths.RESULTS_FILE}: {e}")
                return False
            if not isinstance(data, dict) or not isinstance(data.get("chests"), list):
                logger.error("\u0421\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0438\u0435 \u0441\u043a\u0430\u043d\u0430 \u043e\u0442\u043c\u0435\u043d\u0435\u043d\u043e: \u043d\u0435\u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u0430\u044f \u0441\u0442\u0440\u0443\u043a\u0442\u0443\u0440\u0430 chests.json; \u0438\u0441\u0445\u043e\u0434\u043d\u044b\u0439 \u0444\u0430\u0439\u043b \u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d")
                return False
        else:
            data = {"chests": []}

        try:
            existing = self._find_existing_scan(
                cd.get("id") if isinstance(cd, dict) else None,
                data["chests"])
        except ValueError as exc:
            logger.error(f"Scan save cancelled: {exc}")
            return False
        if existing is not None:
            # JSON is authoritative: a retry must not replace an already committed
            # record with a different payload that happens to reuse its ID.
            try:
                storage.add_chest(paths.RESULTS_DB, existing)
            except Exception as e:
                logger.warning(f"SQLite cache repair for existing scan failed: {e}")
            logger.info(f"Повторное сохранение пропущено: скан {existing.get('id')}")
            return True

        data["chests"].append(cd)
        try:
            save_json(paths.RESULTS_FILE, data)
        except (OSError, TypeError, ValueError) as e:
            logger.error(f"\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442 \u0441\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u044f \u0432 JSON: {e}")
            return False

        # JSON ? ???????? ???????; SQLite ? ????????????????? ??????.
        try:
            if not storage.add_chest(paths.RESULTS_DB, cd):
                logger.warning("\u0420\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442 \u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d \u0432 JSON, \u043d\u043e \u043d\u0435 \u0434\u043e\u0431\u0430\u0432\u043b\u0435\u043d \u0432 SQLite; \u0438\u043d\u0434\u0435\u043a\u0441 \u0431\u0443\u0434\u0435\u0442 \u0432\u043e\u0441\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d \u043f\u0440\u0438 \u043e\u0442\u043a\u0440\u044b\u0442\u0438\u0438 \u0441\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0438")
        except Exception as e:
            logger.error(f"SQLite-\u0437\u0430\u043f\u0438\u0441\u044c \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442\u0430: {e}")
        return True

    def _read_history_sources(self):
        """Read current results and archives without hiding a broken main file."""
        sources = []
        if os.path.exists(paths.RESULTS_FILE):
            try:
                with open(paths.RESULTS_FILE, "r", encoding="utf-8") as stream:
                    data = json.load(stream)
            except (OSError, json.JSONDecodeError, UnicodeError) as exc:
                logger.error(f"History read failed: {paths.RESULTS_FILE}: {exc}")
                return None
            if not isinstance(data, dict) or not isinstance(data.get("chests"), list):
                logger.error("History read failed: invalid chests.json structure")
                return None
            sources.append((paths.RESULTS_FILE, data))

        for archive_path in sorted(glob.glob(os.path.join(paths.HISTORY_DIR, "report_*.json"))):
            try:
                with open(archive_path, "r", encoding="utf-8") as stream:
                    data = json.load(stream)
            except (OSError, json.JSONDecodeError, UnicodeError) as exc:
                logger.error(f"Undo scan cancelled: unreadable history archive {archive_path}: {exc}")
                return None
            if not isinstance(data, dict) or not isinstance(data.get("chests"), list):
                logger.error(f"Undo scan cancelled: invalid archive structure: {archive_path}")
                return None
            sources.append((archive_path, data))
        return sources

    @staticmethod
    def _history_records(sources):
        """Return unique scan records, preferring current JSON over archive copies."""
        records = []
        seen_ids = set()
        order = 0
        for source_path, data in sources:
            for index, chest in enumerate(data.get("chests", [])):
                if not isinstance(chest, dict):
                    continue
                chest_id = chest.get("id")
                if chest_id is not None:
                    chest_id = str(chest_id)
                    if chest_id in seen_ids:
                        continue
                    seen_ids.add(chest_id)
                records.append((chest, source_path, data, index, order))
                order += 1
        return records

    @staticmethod
    def _history_sort_key(record):
        chest, _path, _data, _index, order = record
        return history_sort_key(chest.get("timestamp", ""), order)

    def latest_scan(self):
        """Return the newest scan across current results and all readable archives."""
        with results_history_guard(_RESULTS_LOCK_PATH):
            sources = self._read_history_sources()
            if sources is None:
                return None
            records = self._history_records(sources)
            if not records:
                return None
            return max(records, key=self._history_sort_key)[0]

    def undo_last_scan(self, expected_scan=None):
        """Remove the newest scan, optionally only if it matches the confirmed snapshot."""
        with results_history_guard(_RESULTS_LOCK_PATH):
            sources = self._read_history_sources()
            if sources is None:
                return None
            records = self._history_records(sources)
            if not records:
                logger.info("Undo scan: history is empty.")
                return None

            chest, source_path, source_data, index, _order = max(
                records, key=self._history_sort_key)
            if expected_scan is not None:
                expected_id = expected_scan.get("id") if isinstance(expected_scan, dict) else None
                actual_id = chest.get("id")
                if expected_id is not None:
                    matches = actual_id is not None and str(actual_id) == str(expected_id)
                else:
                    matches = chest == expected_scan
                if not matches:
                    logger.info("Undo scan cancelled: confirmed scan is no longer newest.")
                    return None
            source_data["chests"].pop(index)
            try:
                save_json(source_path, source_data)
            except (OSError, TypeError, ValueError) as exc:
                logger.error(f"Undo scan cancelled: could not save {source_path}: {exc}")
                return None

            remaining = self._history_records(sources)
            try:
                storage.sync_from_json(
                    paths.RESULTS_DB, [record[0] for record in remaining])
            except Exception as exc:
                # JSON is authoritative; SQLite can be rebuilt on the next statistics load.
                logger.warning(f"Undo scan saved to JSON but SQLite refresh failed: {exc}")

            logger.info(
                f"Scan undone: {chest.get('timestamp', '?')}, "
                f"slots: {chest.get('occupied_slots', '?')}"
            )
            return chest

    def scan_chest(self, force=False, auto_scan=False):
        if not self._scan_lock.acquire(blocking=False):
            return None
        try:
            # Reset and publish active state atomically against cancel requests.
            with self._scan_state_lock:
                # A stop request must not be erased by a scan that was queued
                # after the auto worker's last active-state check.
                if auto_scan and self._auto_scan_stop_event.is_set():
                    return None
                self._scan_running = True
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
            # Commit the image hash only after the complete result is saved.
            # Otherwise a failed/cancelled scan would suppress retries of the
            # unchanged chest during subsequent auto-scan iterations.
            slots = self.get_slots(screen)
            slot_list, totals, unknowns, categories = [], {}, set(), {}
            quantity_unknown_slots = 0

            # Параллельная обработка слотов
            max_workers = min(8, max(1, len(slots)))
            results = []
            processing_error = False
            done = 0
            total_slots = len(slots)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(self._process_slot, s): s for s in slots}
                for future in as_completed(futures):
                    if self._scan_cancel_event.is_set():
                        self._scan_cancelled = True
                        self.last_scan_reason = "cancelled"
                        logger.info("Скан отменён пользователем")
                        for pending in futures:
                            pending.cancel()
                        return None
                    try:
                        r = future.result()
                        if r is not None:
                            results.append(r)
                    except Exception as e:
                        processing_error = True
                        logger.error(f"Ошибка обработки слота: {e}", exc_info=True)
                    done += 1
                    if self.scan_progress_cb is not None:
                        try:
                            self.scan_progress_cb(done, total_slots)
                        except Exception as e:
                            logger.error(f"Ошибка колбэка прогресса: {e}", exc_info=True)

            if processing_error:
                self.last_scan_reason = "processing_error"
                logger.error("\u0421\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u043f\u0440\u0435\u0440\u0432\u0430\u043d\u043e: \u043d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u0430\u0442\u044c \u043e\u0434\u043d\u0443 \u0438\u043b\u0438 \u043d\u0435\u0441\u043a\u043e\u043b\u044c\u043a\u043e \u044f\u0447\u0435\u0435\u043a; \u0447\u0430\u0441\u0442\u0438\u0447\u043d\u044b\u0439 \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442 \u043d\u0435 \u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d.")
                return None

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
                # OCR returns a positive integer or None. Treat malformed values
                # as unknown instead of allowing bool/float/zero into totals.
                if (qty is not None and
                        (isinstance(qty, bool) or not isinstance(qty, int) or qty < 1)):
                    logger.warning("???????????? ?????????? ??? %s ? ????? (%s, %s): %r",
                                   name, r.get("row"), r.get("column"), qty)
                    qty = None
                    r["quantity"] = None
                cat = r.get("category", "uncategorized")

                if r["unknown"]:
                    unknowns.add(name)

                slot_list.append({
                    "row": r["row"], "column": r["column"],
                    "item": name, "quantity": qty,
                    "unknown": r["unknown"], "category": cat,
                    "similarity_score": r["similarity_score"]})

                if qty is None:
                    quantity_unknown_slots += 1
                else:
                    totals[name] = totals.get(name, 0) + qty
                    categories.setdefault(cat, {})
                    categories[cat][name] = categories[cat].get(name, 0) + qty

                r.pop("_icon", None)
                r.pop("_slot_image", None)

            if self._scan_cancel_event.is_set():
                self._scan_cancelled = True
                self.last_scan_reason = "cancelled"
                return None

            cd = {
                "id": str(uuid.uuid4()),
                "timestamp": now_moscow().strftime("%d.%m.%Y %H:%M:%S"),
                "screen_resolution": [screen.shape[1], screen.shape[0]],
                "occupied_slots": len(slot_list),
                "unique_items": len({slot["item"] for slot in slot_list if slot.get("item")}),
                "unknown_items": sorted(unknowns),
                "quantity_unknown_slots": quantity_unknown_slots,
                "slots": slot_list,
                "totals": totals,
                "categories": categories,
            }
            if not self.save_chest_result(cd):
                self.last_scan_reason = "storage_error"
                return None
            self.last_chest_hash = ch
            self.play_success_sound()
            logger.info(f"Сундук считан: {len(slot_list)} ячеек, {len(totals)} уникальных")
            return cd
        finally:
            with self._scan_state_lock:
                self._scan_running = False
            self._scan_lock.release()

    @property
    def auto_scan_active(self):
        return self._auto_scan_active

    def start_auto_scan(self):
        # Serialize check-and-publish so concurrent callers cannot spawn two loops.
        with self._auto_scan_state_lock:
            if self._auto_scan_active:
                return
            with self._scan_state_lock:
                self._auto_scan_stop_event.clear()
            previous = self._auto_scan_thread
            if previous is not None and previous.is_alive():
                logger.warning("???????? ??? ????????? ?????????? ????????; ????????? ?????? ????????")
                return
            thread = threading.Thread(target=self._auto_scan_loop, daemon=True)
            self._auto_scan_active = True
            self._auto_scan_thread = thread
            try:
                thread.start()
            except Exception:
                self._auto_scan_active = False
                if self._auto_scan_thread is thread:
                    self._auto_scan_thread = None
                logger.exception("Auto-scan worker could not be started")
                raise
        logger.info("???????? ???????")

    def stop_auto_scan(self, timeout=5.0):
        with self._auto_scan_state_lock:
            self._auto_scan_active = False
            # Synchronize with scan_chest initialization. If the scan has not
            # started yet, its startup guard will refuse to clear this stop.
            with self._scan_state_lock:
                self._auto_scan_stop_event.set()
            t = self._auto_scan_thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=max(0.0, float(timeout)))
        stopped = t is None or not t.is_alive()
        with self._auto_scan_state_lock:
            if stopped and self._auto_scan_thread is t:
                self._auto_scan_thread = None
        if stopped:
            logger.info("???????? ??????????")
        else:
            logger.warning("???????? ??? ????????? ??????? ????????")
        return stopped

    def _auto_scan_loop(self):
        try:
            while self._auto_scan_active:
                try:
                    interval = float(self.config.get("scan_interval_seconds", 2))
                    if not math.isfinite(interval):
                        raise ValueError("scan interval must be finite")
                    interval = max(0.5, interval)
                except (TypeError, ValueError, OverflowError) as e:
                    interval = 2.0
                    logger.warning(f"\u041d\u0435\u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u044b\u0439 \u0438\u043d\u0442\u0435\u0440\u0432\u0430\u043b \u0430\u0432\u0442\u043e\u0441\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u044f; \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0435\u0442\u0441\u044f 2 \u0441: {e}")

                try:
                    result = None
                    if self._auto_scan_active and not self._auto_scan_stop_event.is_set():
                        chest_open = self.chest_is_open_fast()
                        if self._auto_scan_active and not self._auto_scan_stop_event.is_set():
                            if chest_open:
                                result = self.scan_chest(force=False, auto_scan=True)
                            elif self.chest_was_open:
                                self.chest_was_open = False
                                self.last_chest_hash = None
                                self._last_chest_pos = None
                    if result:
                        logger.info(f"\u0410\u0432\u0442\u043e\u0441\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435: {result['occupied_slots']} \u044f\u0447\u0435\u0435\u043a")
                except Exception as e:
                    logger.error(f"\u041e\u0448\u0438\u0431\u043a\u0430 \u0430\u0432\u0442\u043e\u0441\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u044f: {e}", exc_info=True)

                waited = 0.0
                while self._auto_scan_active and waited < interval:
                    delay = min(0.2, interval - waited)
                    time.sleep(delay)
                    waited += delay
        finally:
            with self._auto_scan_state_lock:
                self._auto_scan_active = False
                if self._auto_scan_thread is threading.current_thread():
                    self._auto_scan_thread = None
