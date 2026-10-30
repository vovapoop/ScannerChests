"""GUI settings: окно настроек с навигацией и сохранением (PySide6)."""
import os
import threading

import requests
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from chest_scanner import paths
from chest_scanner.commons import save_json
from chest_scanner.gui.theme import PALETTE
from chest_scanner.gui.widgets import (
    HoverButton,
    SettingCard,
    TemplateSelector,
    entry_row,
    make_label,
)

NAV_ITEMS = [
    ("general", "⚙  Основные"),
    ("detection", "🎯  Распознавание"),
    ("grid", "🧱  Сетка"),
    ("templates", "🖼  Шаблоны"),
    ("discord", "📨  Discord"),
]


class SettingsDialog(QDialog):
    """Настройки: разделы слева, содержимое справа, «Сохранить» внизу."""

    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self.reader = main.reader
        self._settings_win = self
        self.setWindowTitle("Настройки")
        self.resize(1060, 700)
        self.setMinimumSize(940, 600)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 16)
        root.setSpacing(12)
        root.addWidget(make_label("Настройки", title=True))

        body = QHBoxLayout()
        body.setSpacing(14)
        root.addLayout(body, 1)

        self.nav = QListWidget()
        self.nav.setObjectName("navList")
        self.nav.setFixedWidth(210)
        for _key, title in NAV_ITEMS:
            self.nav.addItem(title)
        self.nav.currentRowChanged.connect(self._show_page)
        body.addWidget(self.nav)

        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        bottom.addWidget(make_label(
            "Изменения применяются после «Сохранить».", muted=True, small=True))
        bottom.addStretch(1)
        close_btn = HoverButton("Закрыть")
        close_btn.clicked.connect(self.reject)
        bottom.addWidget(close_btn)
        save_btn = HoverButton("Сохранить", accent=True)
        save_btn.clicked.connect(self._save)
        bottom.addWidget(save_btn)
        root.addLayout(bottom)

        self._init_vars()
        self._build_pages()

        self.nav.setCurrentRow(0)
        self._show_page(0)

    # ── Значения полей из конфига ──
    def _init_vars(self):
        cfg = self.reader.config
        grid = cfg.get("chest_grid", paths.DEFAULT_CONFIG["chest_grid"])
        dm = cfg.get("discord_message", {})
        v = {}
        ref = cfg.get("reference_screen_size", [1920, 1080])
        v["ref_w"] = QLineEdit(str(ref[0] if isinstance(ref, list) and len(ref) > 0 else 1920))
        v["ref_h"] = QLineEdit(str(ref[1] if isinstance(ref, list) and len(ref) > 1 else 1080))
        v["scan_interval"] = self._slider(0.5, 10.0, float(cfg.get("scan_interval_seconds", 2)), 10, "{:.1f}")
        v["print_candidates"] = QCheckBox()
        v["print_candidates"].setChecked(bool(cfg.get("print_item_candidates", False)))
        v["auto_scan"] = QCheckBox()
        v["auto_scan"].setChecked(bool(cfg.get("auto_scan_enabled", False)))
        v["item_detect_cache"] = QCheckBox()
        v["item_detect_cache"].setChecked(bool(cfg.get("item_detect_cache", True)))
        v["tray_minimize"] = QCheckBox()
        v["tray_minimize"].setChecked(bool(cfg.get("tray_minimize_to_tray", False)))
        v["notify_scan"] = QCheckBox()
        v["notify_scan"].setChecked(bool(cfg.get("notify_on_scan", True)))
        v["chest_threshold"] = self._slider(0.5, 0.99, float(cfg.get("chest_template_threshold", 0.88)), 100, "{:.2f}")
        v["empty_threshold"] = self._slider(100, 5000, float(cfg.get("empty_slot_threshold", 600)), 1, "{:.0f}")
        v["item_threshold"] = self._slider(100, 8000, float(cfg.get("item_match_threshold", 1500)), 1, "{:.0f}")
        v["item_margin"] = self._slider(0, 1000, float(cfg.get("item_match_margin", 200)), 1, "{:.0f}")
        v["phash_threshold"] = self._slider(0, 64, int(cfg.get("phash_threshold", 14)), 1, "{:.0f}")
        # В конфиге ключи называются "columns"/"rows", а не "cols"/"rows" —
        # сопоставляем явно, иначе при открытии настроек терялись значения.
        for key in ("grid_x1", "grid_y1", "grid_x2", "grid_y2"):
            v[key] = QLineEdit(str(grid.get(key[5:], 0)))
        v["grid_cols"] = QLineEdit(str(grid.get("columns", 0)))
        v["grid_rows"] = QLineEdit(str(grid.get("rows", 0)))
        icon = cfg.get("icon_roi_in_slot", paths.DEFAULT_CONFIG["icon_roi_in_slot"])
        count = cfg.get("count_roi_in_slot", paths.DEFAULT_CONFIG["count_roi_in_slot"])
        for i in range(4):
            v[f"icon_{i}"] = QLineEdit(str(icon[i] if i < len(icon) else 0))
            v[f"count_{i}"] = QLineEdit(str(count[i] if i < len(count) else 0))
        v["chest_template"] = TemplateSelector(
            value=str(cfg.get("chest_template", "assets/chest_open.png")),
            title="Шаблон сундука")
        v["empty_template"] = TemplateSelector(
            value=str(cfg.get("empty_slot_template", "assets/empty_slot.png")),
            title="Пустой слот")
        v["webhook"] = QLineEdit(self.reader.get_discord_webhook_url() or "")
        v["webhook"].setPlaceholderText("https://discord.com/api/webhooks/...")
        v["webhook"].setProperty("mono", True)
        v["discord_env"] = QLineEdit(str(cfg.get("discord_webhook_env", "DISCORD_WEBHOOK_URL")))
        v["max_items"] = QLineEdit(str(cfg.get("discord_max_items_in_message", 25)))
        v["delete_old"] = QCheckBox()
        v["delete_old"].setChecked(bool(cfg.get("discord_delete_old_messages", True)))
        v["stack_size"] = QLineEdit(str(dm.get("stack_size", 64)))
        hk = cfg.get("hotkeys", {}) if isinstance(cfg.get("hotkeys"), dict) else {}
        for action in ("toggle_window", "scan", "discord", "exit"):
            v[f"hk_{action}"] = QLineEdit(str(hk.get(action, "") or ""))
        self._vars = v

    def _slider(self, lo, hi, value, factor, fmt):
        """QSlider с подписью значения справа."""
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        s = QSlider(Qt.Horizontal)
        s.setRange(int(lo * factor), int(hi * factor))
        s.setValue(int(max(lo, min(hi, value)) * factor))
        lbl = QLabel(fmt.format(float(s.value()) / factor))
        lbl.setProperty("muted", True)
        lbl.setFixedWidth(60)
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        def on_change(val):
            try:
                lbl.setText(fmt.format(float(val) / factor))
            except Exception:
                pass

        s.valueChanged.connect(on_change)
        lay.addWidget(s, 1)
        lay.addWidget(lbl)
        w._slider = s
        w._format = fmt
        w._factor = factor
        return w

    def _slider_value(self, widget):
        return float(widget._slider.value()) / widget._factor

    # ── Страницы ──
    def _build_pages(self):
        v = self._vars
        builders = {
            "general": self._page_general,
            "detection": self._page_detection,
            "grid": self._page_grid,
            "templates": self._page_templates,
            "discord": self._page_discord,
        }
        for key, _title in NAV_ITEMS:
            page = QWidget()
            outer = QVBoxLayout(page)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(12)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            inner = QWidget()
            lay = QVBoxLayout(inner)
            lay.setContentsMargins(2, 2, 2, 2)
            lay.setSpacing(12)
            builders[key](lay, v)
            lay.addStretch(1)
            scroll.setWidget(inner)
            outer.addWidget(scroll)
            self.stack.addWidget(page)

    def _show_page(self, row):
        if 0 <= row < self.stack.count():
            self.stack.setCurrentIndex(row)

    def _page_general(self, lay, v):
        card = SettingCard("Эталонное разрешение",
                           "Сетка масштабируется от этого разрешения.")
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(make_label("Ширина", muted=True))
        v["ref_w"].setFixedWidth(90)
        row.addWidget(v["ref_w"])
        row.addSpacing(12)
        row.addWidget(make_label("Высота", muted=True))
        v["ref_h"].setFixedWidth(90)
        row.addWidget(v["ref_h"])
        row.addStretch(1)
        card.content.addLayout(row)
        lay.addWidget(card)

        card2 = SettingCard("Сканирование")
        self._add_check(card2, "Автосканирование при запуске", v["auto_scan"])
        self._add_check(card2, "Показывать кандидатов в логе", v["print_candidates"])
        self._add_check(card2, "Кэш распознавания (ускоряет повторные сканы)",
                        v["item_detect_cache"])
        self._add_slider_row(card2, "Интервал автосканирования, сек.",
                             v["scan_interval"])
        lay.addWidget(card2)

        card_t = SettingCard("Трей и уведомления")
        self._add_check(card_t, "Сворачивать в трей при закрытии окна", v["tray_minimize"])
        self._add_check(card_t, "Уведомлять о результате сканирования", v["notify_scan"])
        lay.addWidget(card_t)

        card3 = SettingCard("Горячие клавиши",
                            "Пустое поле отключает действие. "
                            "Имена клавиш: f1–f12, цифры, буквы.")
        for label, key in [("Показать окно", "hk_toggle_window"),
                           ("Сканирование", "hk_scan"), ("Discord", "hk_discord"),
                           ("Выход", "hk_exit")]:
            v[key].setFixedWidth(120)
            self._add_entry(card3, label, v[key])
        lay.addWidget(card3)

    def _page_detection(self, lay, v):
        card = SettingCard("Пороги распознавания (SSIM)",
                           "Меньше расстояние — сильнее похожи изображения.")
        rows = [("Порог шаблона сундука", "chest_threshold"),
                ("Порог пустого слота", "empty_threshold"),
                ("Порог совпадения предмета", "item_threshold"),
                ("Порог неоднозначности", "item_margin"),
                ("Порог pHash (грубый отбор, 0–64)", "phash_threshold")]
        for label, key in rows:
            self._add_slider_row(card, label, v[key])
        lay.addWidget(card)

    def _page_grid(self, lay, v):
        card = SettingCard("Область сундука (два угла)",
                           "Левый верхний (X1, Y1) и правый нижний (X2, Y2).")
        fields = [("X1 (лево)", "grid_x1"), ("Y1 (верх)", "grid_y1"),
                  ("X2 (право)", "grid_x2"), ("Y2 (низ)", "grid_y2"),
                  ("Столбцы", "grid_cols"), ("Строки", "grid_rows")]
        for label, key in fields:
            v[key].setFixedWidth(90)
            self._add_entry(card, label, v[key])
        btns = QHBoxLayout()
        cal = HoverButton("Калибровка кликами")
        cal.clicked.connect(self._safe_calibrate)
        btns.addWidget(cal)
        auto = HoverButton("Автоопределение")
        auto.clicked.connect(self._safe_auto_detect_grid)
        btns.addWidget(auto)
        dbg = HoverButton("Отладка сетки")
        dbg.clicked.connect(self._safe_grid_debug)
        btns.addWidget(dbg)
        btns.addStretch(1)
        card.content.addSpacing(6)
        card.content.addLayout(btns)
        lay.addWidget(card)

        presets = SettingCard("Пресеты контейнеров")
        pres_row = QHBoxLayout()
        pres_row.setSpacing(8)
        for label, cols, rows in [("Двойной сундук (9×6)", 9, 6),
                                  ("Одиночный сундук (9×3)", 9, 3)]:
            b = HoverButton(label)
            b.clicked.connect(lambda _=False, c=cols, r=rows: self._apply_preset(c, r))
            pres_row.addWidget(b)
        pres_row.addStretch(1)
        presets.content.addLayout(pres_row)
        lay.addWidget(presets)

        roi = QHBoxLayout()
        roi.setSpacing(12)
        ci = SettingCard("Иконка предмета", "Доли от размера слота (0.0–1.0).")
        cc = SettingCard("Количество", "Область цифр в углу слота.")
        for i, label in enumerate(["X", "Y", "Ширина", "Высота"]):
            v[f"icon_{i}"].setFixedWidth(80)
            v[f"count_{i}"].setFixedWidth(80)
            self._add_entry(ci, label, v[f"icon_{i}"])
            self._add_entry(cc, label, v[f"count_{i}"])
        roi.addWidget(ci, 1)
        roi.addWidget(cc, 1)
        lay.addLayout(roi)

    def _page_templates(self, lay, v):
        card = SettingCard("Шаблон сундука",
                           "Файл для определения открытого сундука.")
        card.content.addWidget(v["chest_template"])
        lay.addWidget(card)

        card2 = SettingCard("Шаблон пустого слота",
                            "Файл для определения пустых ячеек.")
        card2.content.addWidget(v["empty_template"])
        lay.addWidget(card2)

        card3 = SettingCard("Автогенерация",
                            "Создаёт шаблоны из текущего экрана. Сундук должен быть открыт.")
        b = HoverButton("Создать недостающие")
        b.clicked.connect(self._safe_auto_templates)
        card3.content.addWidget(b)
        lay.addWidget(card3)

    def _page_discord(self, lay, v):
        card = SettingCard("Webhook")
        self._add_entry(card, "URL", v["webhook"])
        self.status_lbl = make_label("", muted=True, small=True)
        card.content.addWidget(self.status_lbl)
        test_btn = HoverButton("Проверить")
        test_btn.clicked.connect(self._test_webhook)
        card.content.addWidget(test_btn)
        lay.addWidget(card)

        card2 = SettingCard("Параметры отчёта")
        self._add_entry(card2, "Размер стака по умолчанию", v["stack_size"])
        self._add_entry(card2, "Максимум предметов", v["max_items"])
        self._add_check(card2, "Удалять лишние старые сообщения", v["delete_old"])
        self._add_entry(card2, "Переменная окружения", v["discord_env"])
        lay.addWidget(card2)

    # ── Хелперы разметки ──
    def _add_check(self, card, label, widget):
        widget.setText(label)
        card.content.addWidget(widget)

    def _add_entry(self, card, label, widget):
        entry_row(card.content, label, widget, stretch=1)

    def _add_slider_row(self, card, label, widget):
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(make_label(label, muted=True), 1)
        row.addWidget(widget, 1)
        card.content.addLayout(row)

    # ── Действия из настроек ──
    def _safe(self, func):
        try:
            func()
        except Exception as e:
            self.main._log(f"Ошибка: {e}", PALETTE["RED"])

    def _safe_calibrate(self):
        self._safe(self.main._calibrate)

    def _safe_auto_detect_grid(self):
        self._safe(self.main._auto_detect_grid)

    def _safe_grid_debug(self):
        self._safe(self.main._run_grid_debug)

    def _safe_auto_templates(self):
        self._safe(self.main._auto_templates)

    def _apply_preset(self, columns, rows):
        self._vars["grid_cols"].setText(str(columns))
        self._vars["grid_rows"].setText(str(rows))

    def _test_webhook(self):
        url = self._vars["webhook"].text().strip()
        if not url or not url.startswith(("https://discord.com/api/webhooks/",
                                          "https://discordapp.com/api/webhooks/")):
            self.status_lbl.setText("Некорректный URL")
            self.status_lbl.setStyleSheet(f"color: {PALETTE['RED']};")
            return
        self.status_lbl.setText("Проверка...")
        self.status_lbl.setStyleSheet(f"color: {PALETTE['YELLOW']};")

        def worker():
            try:
                r = requests.get(url, timeout=10)
                ok = r.status_code == 200
                msg = "Webhook доступен." if ok else f"HTTP {r.status_code}"
            except requests.RequestException as e:
                ok, msg = False, str(e)

            def apply():
                try:
                    self.status_lbl.setText(msg)
                    self.status_lbl.setStyleSheet(
                        f"color: {PALETTE['GREEN'] if ok else PALETTE['RED']};")
                except Exception:
                    pass

            QTimer.singleShot(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    # ── Сохранение ──
    def _parse_int(self, widget, name):
        try:
            return int(float(str(widget.text()).strip()))
        except Exception:
            raise ValueError(f"{name}: нужно целое число.")

    def _parse_float(self, widget, name):
        try:
            return float(str(widget.text()).strip())
        except Exception:
            raise ValueError(f"{name}: нужно число.")

    def _save(self):
        v = self._vars
        try:
            rw = self._parse_int(v["ref_w"], "Ширина")
            rh = self._parse_int(v["ref_h"], "Высота")
            if rw < 320 or rh < 240:
                raise ValueError("Разрешение слишком маленькое.")
            si = self._slider_value(v["scan_interval"])
            if si <= 0:
                raise ValueError("Интервал должен быть > 0.")
            grid = {"x1": self._parse_int(v["grid_x1"], "X1"),
                    "y1": self._parse_int(v["grid_y1"], "Y1"),
                    "x2": self._parse_int(v["grid_x2"], "X2"),
                    "y2": self._parse_int(v["grid_y2"], "Y2"),
                    "columns": self._parse_int(v["grid_cols"], "Столбцы"),
                    "rows": self._parse_int(v["grid_rows"], "Строки")}
            if grid["x2"] <= grid["x1"] or grid["y2"] <= grid["y1"]:
                raise ValueError("X2/Y2 должны быть больше X1/Y1.")
            if grid["columns"] < 1 or grid["rows"] < 1:
                raise ValueError("Столбцы и строки должны быть > 0.")
            names = ["X", "Y", "Ширина", "Высота"]
            icon = [self._parse_float(v[f"icon_{i}"], f"Иконка: {names[i]}") for i in range(4)]
            count = [self._parse_float(v[f"count_{i}"], f"Количество: {names[i]}") for i in range(4)]
            if icon[2] <= 0 or icon[3] <= 0 or count[2] <= 0 or count[3] <= 0:
                raise ValueError("Ширина и высота областей должны быть > 0.")
            max_items = self._parse_int(v["max_items"], "Максимум предметов")
            stack_size = self._parse_int(v["stack_size"], "Размер стака")
            if max_items < 1 or stack_size < 1:
                raise ValueError("Значения должны быть > 0.")
            hotkeys = {}
            for action in ("toggle_window", "scan", "discord", "exit"):
                hotkeys[action] = v[f"hk_{action}"].text().strip().lower()
        except ValueError as e:
            QMessageBox.critical(self, "Настройки", str(e))
            return

        webhook = v["webhook"].text().strip()
        if webhook and not webhook.startswith(("https://discord.com/api/webhooks/",
                                               "https://discordapp.com/api/webhooks/")):
            QMessageBox.critical(self, "Discord", "Некорректный Webhook URL.")
            return

        cfg = self.reader.config
        cfg["reference_screen_size"] = [rw, rh]
        cfg["scan_interval_seconds"] = si
        cfg["print_item_candidates"] = bool(v["print_candidates"].isChecked())
        cfg["auto_scan_enabled"] = bool(v["auto_scan"].isChecked())
        cfg["chest_template_threshold"] = self._slider_value(v["chest_threshold"])
        cfg["empty_slot_threshold"] = self._slider_value(v["empty_threshold"])
        cfg["item_match_threshold"] = self._slider_value(v["item_threshold"])
        cfg["item_match_margin"] = self._slider_value(v["item_margin"])
        cfg["phash_threshold"] = int(self._slider_value(v["phash_threshold"]))
        cfg["item_detect_cache"] = bool(v["item_detect_cache"].isChecked())
        cfg["tray_minimize_to_tray"] = bool(v["tray_minimize"].isChecked())
        cfg["notify_on_scan"] = bool(v["notify_scan"].isChecked())
        cfg["chest_grid"] = grid
        cfg["icon_roi_in_slot"] = icon
        cfg["count_roi_in_slot"] = count
        cfg["chest_template"] = v["chest_template"].value() or "assets/chest_open.png"
        cfg["empty_slot_template"] = v["empty_template"].value() or "assets/empty_slot.png"
        cfg["discord_webhook_env"] = v["discord_env"].text().strip() or "DISCORD_WEBHOOK_URL"
        cfg["discord_max_items_in_message"] = max_items
        cfg["discord_delete_old_messages"] = bool(v["delete_old"].isChecked())
        cfg.setdefault("discord_message", {})["stack_size"] = stack_size
        cfg["hotkeys"] = hotkeys

        if webhook:
            save_json(paths.DISCORD_WEBHOOK_FILE, {"webhook_url": webhook})
        else:
            try:
                if os.path.exists(paths.DISCORD_WEBHOOK_FILE):
                    os.remove(paths.DISCORD_WEBHOOK_FILE)
            except OSError:
                pass

        save_json(paths.CONFIG_FILE, cfg)
        main = self.main
        was_auto = main.reader.auto_scan_active
        if was_auto:
            main.reader.stop_auto_scan()
        main.reader.reload_profile()
        if cfg.get("auto_scan_enabled", False):
            main.reader.start_auto_scan()
        main._sync_auto_scan_btn()
        main._refresh_profile_label()
        main._refresh_stats()
        try:
            main.hotkeys_hint_label.setText(main._hotkeys_hint())
        except Exception:
            pass
        main._log("✓ Настройки сохранены.", PALETTE["GREEN"])
        main._refresh_hotkeys()
        self.accept()
