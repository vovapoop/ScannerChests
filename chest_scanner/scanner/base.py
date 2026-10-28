"""ChestReader mixin: базовое состояние и инициализация."""
import threading

from chest_scanner.app_bootstrap import WINSOUND_AVAILABLE, winsound
from chest_scanner.commons import ensure_directories, load_json, save_json
from chest_scanner.config import activate_profile, load_profiles, validate_config
from chest_scanner.items import normalize_items_db
from chest_scanner.logging_setup import logger
from chest_scanner import paths
from chest_scanner.template_cache import template_cache

class ScannerBaseMixin:
    """Methods extracted from the original ChestReader."""

    def __init__(self):
        profiles = load_profiles()
        activate_profile(profiles.get("active", "Default"))
        ensure_directories()

        raw_config = load_json(paths.CONFIG_FILE, {})
        self.config, config_warnings = validate_config(raw_config)
        for w in config_warnings:
            logger.warning(f"[КОНФИГ] {w}")
        save_json(paths.CONFIG_FILE, self.config)

        self.items_db = normalize_items_db(load_json(paths.ITEMS_FILE, {"items": []}))
        save_json(paths.ITEMS_FILE, self.items_db)

        self.template_cache = template_cache

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
        self._scan_lock = threading.Lock()
        self._auto_scan_active = False
        self._auto_scan_thread = None
        self._last_chest_pos = None
        self._stack_cache = {}
        self._rebuild_stack_cache()
        self.print_startup_status()

    def _rebuild_stack_cache(self):
        self._stack_cache = {}
        for item in self.items_db.get("items", []):
            n = item.get("name")
            if n:
                self._stack_cache[n] = item.get("stack_size", 64)

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

    def reload_profile(self):
        ensure_directories()
        raw = load_json(paths.CONFIG_FILE, {})
        self.config, warns = validate_config(raw)
        for w in warns:
            logger.warning(f"[КОНФИГ] {w}")
        save_json(paths.CONFIG_FILE, self.config)
        self.items_db = normalize_items_db(load_json(paths.ITEMS_FILE, {"items": []}))
        save_json(paths.ITEMS_FILE, self.items_db)
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
        self._rebuild_stack_cache()

