"""ChestReader mixin: базовое состояние и инициализация."""
import threading
from contextlib import contextmanager, nullcontext

from chest_scanner import paths, storage
from chest_scanner.app_bootstrap import WINSOUND_AVAILABLE, winsound
from chest_scanner.commons import (
    ensure_directories,
    load_json_recover,
    results_history_guard,
    save_json,
)
from chest_scanner.config import (
    activate_profile,
    load_profiles,
    profile_operation_guard,
    save_profiles,
    validate_config,
)
from chest_scanner.image_utils import phash, preprocess_icon
from chest_scanner.items import normalize_items_db
from chest_scanner.logging_setup import logger
from chest_scanner.screenshot import (
    capture_settings,
    configure_capture,
)
from chest_scanner.template_cache import template_cache


class ScannerBaseMixin:
    """Methods extracted from the original ChestReader."""

    #: Порог расстояния Хэмминга для грубого отбора кандидатов по pHash
    #: (указывается в конфиге как "phash_threshold").
    DEFAULT_PHASH_THRESHOLD = 14

    def __init__(self):
        profiles = load_profiles()
        activate_profile(profiles.get("active", "Default"))
        ensure_directories()
        with results_history_guard(paths.RESULTS_FILE):
            # SQLite is a rebuildable cache; a locked or unavailable cache must
            # not prevent the application from starting with authoritative JSON.
            try:
                storage.init_db(paths.RESULTS_DB)
            except (OSError, storage.sqlite3.Error) as exc:
                logger.warning(
                    f"SQLite history cache unavailable at startup; "
                    f"continuing with JSON as authoritative: {exc}")
            else:
                # Recover the cache before this instance starts any scan worker.
                try:
                    if storage.reconcile_from_json(
                            paths.RESULTS_DB, paths.RESULTS_FILE, paths.HISTORY_DIR):
                        logger.info("SQLite history cache reconciled from JSON.")
                except (OSError, UnicodeError, ValueError,
                        storage.sqlite3.Error) as exc:
                    logger.warning(
                        f"Could not reconcile SQLite history cache; "
                        f"JSON remains authoritative: {exc}")

        raw_config = load_json_recover(
            paths.CONFIG_FILE, {}, lambda value: isinstance(value, dict))
        self.config, config_warnings = validate_config(raw_config)
        for w in config_warnings:
            logger.warning(f"[КОНФИГ] {w}")
        configure_capture(self.config.get("capture_source"),
                          self.config.get("game_window_title"))
        save_json(paths.CONFIG_FILE, self.config, backup=True)

        raw_items = load_json_recover(
            paths.ITEMS_FILE, {"items": []},
            lambda value: isinstance(value, dict)
            and isinstance(value.get("items"), list))
        self.items_db = normalize_items_db(raw_items)
        save_json(paths.ITEMS_FILE, self.items_db, backup=True)

        self.template_cache = template_cache
        # Relative image references are reused across profiles. Clear entries
        # from any previous reader/profile before resolving them in this one.
        self.template_cache.invalidate()

        self.ref_w, self.ref_h = self.config["reference_screen_size"]
        self.grid = self.config["chest_grid"]
        self.chest_template = self.read_image(self.config["chest_template"])
        self.empty_slot_template = self.read_image(self.config["empty_slot_template"])
        self.digit_templates = self.load_digit_templates()

        self.last_chest_hash = None
        self.chest_was_open = False
        self.prompted_unknowns = set()
        self.unknown_item_prompt = None
        self.unknown_item_category = "uncategorized"
        self._grid_debug_active = False
        self._scan_lock = threading.RLock()
        self._scan_cancel_event = threading.Event()
        self._scan_state_lock = threading.Lock()
        self._scan_running = False
        self._scan_cancelled = False
        self.scan_progress_cb = None
        self._auto_scan_active = False
        self._auto_scan_thread = None
        self._auto_scan_state_lock = threading.Lock()
        self._auto_scan_stop_event = threading.Event()
        self._last_chest_pos = None
        self._stack_cache = {}
        self._phash_entries = []
        self._detect_cache = {}
        self._rebuild_indexes()
        self.print_startup_status()

    def _rebuild_stack_cache(self):
        self._stack_cache = {}
        for item in self.items_db.get("items", []):
            n = item.get("name")
            if n:
                self._stack_cache[n] = item.get("stack_size", 64)

    def _rebuild_phash_index(self):
        """Строит pHash-индекс включённых предметов для грубого отбора.

        Каждая иконка предмета добавляется отдельной записью, поэтому
        несколько фото одного предмета работают как независимые шаблоны.
        """
        entries = []
        for item in self.items_db.get("items", []):
            if not item.get("enabled", True):
                continue
            name = item.get("name")
            if not name:
                continue
            for path in (item.get("images") or [item.get("image")]):
                if not path:
                    continue
                raw = self.template_cache.get_raw(path)
                if raw is None:
                    continue
                entries.append({"hash": phash(preprocess_icon(raw)), "name": name,
                                "image": path,
                                "category": item.get("category", "uncategorized")})
        self._phash_entries = entries
        logger.debug(f"pHash-индекс: {len(entries)} шаблонов")

    def _rebuild_indexes(self):
        """Перестраивает кэши распознавания после изменения базы или конфига."""
        self._rebuild_stack_cache()
        self._rebuild_phash_index()
        self._detect_cache.clear()

    def get_item_stack_size(self, name, default=64):
        return self._stack_cache.get(name, default)

    def read_image(self, path):
        return self.template_cache.get_raw(path)

    def play_scan_start_sound(self):
        if not WINSOUND_AVAILABLE:
            return
        try:
            winsound.Beep(880, 120)
        except Exception:
            pass

    def play_success_sound(self):
        if not WINSOUND_AVAILABLE:
            return
        try:
            winsound.Beep(880, 120)
            winsound.Beep(1175, 180)
        except Exception:
            pass

    # ── Отмена и прогресс сканирования ──
    def request_scan_cancel(self):
        """Request cancellation, synchronized with scan initialization."""
        with self._scan_state_lock:
            self._scan_cancel_event.set()

    def reset_scan_cancel(self):
        """Сбрасывает флаги отмены перед новым сканированием."""
        self._scan_cancel_event.clear()
        self._scan_cancelled = False

    @property
    def scan_cancelled(self):
        """True, если последнее сканирование было отменено пользователем."""
        return self._scan_cancelled

    def print_startup_status(self):
        g = self.grid
        logger.info("=" * 50)
        logger.info("  СКАНЕР СУНДУКОВ")
        logger.info("=" * 50)
        logger.info(f"Папка: {paths.BASE_DIR}")
        logger.info(f"Разрешение: {self.ref_w}x{self.ref_h}")
        logger.info(f"Сетка: ({g.get('x1')},{g.get('y1')}) -> ({g.get('x2')},{g.get('y2')})")
        logger.info(f"Ячеек: {g.get('columns')}x{g.get('rows')}")
        logger.info(f"Предметов: {len(self.items_db['items'])}")
        logger.info(f"Цифр: {len(self.digit_templates)}/10")
        logger.info(f"Шаблонов в кэше: {self.template_cache.size}")
        if self.chest_template is None:
            logger.warning("Не найден шаблон сундука — выполните калибровку")
        if self.empty_slot_template is None:
            logger.warning("Не найден шаблон пустого слота")
        logger.info("=" * 50)

    @contextmanager
    def _scanner_state_guard(self, blocking=True):
        """Serialize mutations of scanner configuration and recognition state."""
        lock = getattr(self, "_scan_lock", None)
        if lock is None:
            yield True
            return
        acquired = lock.acquire(blocking=blocking)
        try:
            yield acquired
        finally:
            if acquired:
                lock.release()

    def switch_profile(self, name):
        """Wait for scans, then serialize the complete profile transition."""
        scan_lock = getattr(self, "_scan_lock", None)
        with scan_lock if scan_lock is not None else nullcontext():
            with profile_operation_guard():
                self._switch_profile_locked(name)

    def _switch_profile_locked(self, name):
        """Switch profiles transactionally and restore the old state on failure."""
        path_fields = (
            "PROFILE_DIR", "CONFIG_FILE", "ITEMS_FILE", "RESULTS_DIR",
            "UNKNOWN_DIR", "UNKNOWN_QUEUE_FILE", "ITEM_IMAGES_DIR",
            "RESULTS_FILE", "RESULTS_DB", "HISTORY_DIR",
            "DISCORD_IDS_FILE", "DISCORD_WEBHOOK_FILE",
        )
        old_paths = {field: getattr(paths, field) for field in path_fields}
        old_active = load_profiles().get("active", "Default")
        state_fields = (
            "config", "items_db", "ref_w", "ref_h", "grid",
            "chest_template", "empty_slot_template", "digit_templates",
            "last_chest_hash", "chest_was_open", "prompted_unknowns",
            "_last_chest_pos", "_stack_cache", "_phash_entries",
            "_detect_cache",
        )
        old_state = {}
        for field in state_fields:
            if hasattr(self, field):
                value = getattr(self, field)
                if field == "prompted_unknowns":
                    value = set(value)
                elif field == "_detect_cache":
                    value = dict(value)
                old_state[field] = value
        old_capture = capture_settings()

        try:
            activate_profile(name)
            self.reload_profile()
        except Exception:
            for field, value in old_paths.items():
                setattr(paths, field, value)
            for field, value in old_state.items():
                setattr(self, field, value)
            try:
                profile_data = load_profiles()
                profile_data["active"] = old_active
                save_profiles(profile_data)
            except Exception:
                logger.exception("Could not restore active profile in registry")
            try:
                configure_capture(old_capture.get("source"),
                                  old_capture.get("title"))
            except Exception:
                logger.exception("Could not restore capture configuration")
            self.template_cache.invalidate()
            raise

    def reload_profile(self):
        with self._scanner_state_guard(blocking=True):
            return self._reload_profile_locked()

    def _reload_profile_locked(self):
        ensure_directories()
        raw = load_json_recover(
            paths.CONFIG_FILE, {}, lambda value: isinstance(value, dict))
        self.config, warns = validate_config(raw)
        for w in warns:
            logger.warning(f"[КОНФИГ] {w}")
        save_json(paths.CONFIG_FILE, self.config, backup=True)
        # Источник захвата (весь экран / окно игры) применяется при каждой
        # загрузке профиля: старт приложения, смена профиля, «Сохранить».
        configure_capture(self.config.get("capture_source"),
                          self.config.get("game_window_title"))
        logger.info(f"[ЗАХВАТ] {capture_settings()}")
        raw_items = load_json_recover(
            paths.ITEMS_FILE, {"items": []},
            lambda value: isinstance(value, dict)
            and isinstance(value.get("items"), list))
        self.items_db = normalize_items_db(raw_items)
        save_json(paths.ITEMS_FILE, self.items_db, backup=True)
        self.ref_w, self.ref_h = self.config["reference_screen_size"]
        self.grid = self.config["chest_grid"]
        self.template_cache.invalidate()
        self.chest_template = self.read_image(self.config["chest_template"])
        self.empty_slot_template = self.read_image(self.config["empty_slot_template"])
        self.digit_templates = self.load_digit_templates()
        self.last_chest_hash = None
        self.chest_was_open = False
        self.prompted_unknowns.clear()
        self._last_chest_pos = None
        self._rebuild_indexes()
        self.print_startup_status()


