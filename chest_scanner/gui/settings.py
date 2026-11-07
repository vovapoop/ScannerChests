"""GUI settings: окно настроек с навигацией и сохранением (PySide6)."""

import os
import threading
import time

import cv2
import requests
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from chest_scanner import paths
from chest_scanner.commons import absolute_path, save_json
from chest_scanner.gui.theme import PALETTE
from chest_scanner.gui.widgets import (
    HoverButton,
    SettingCard,
    TemplateSelector,
    entry_row,
    make_label,
)
from chest_scanner.screenshot import (
    CAPTURE_SOURCE_WINDOW,
    flush_compositor,
    screenshot_bgr,
)

NAV_ITEMS = [
    ("general", "⚙  Основные"),
    ("detection", "🎯  Распознавание"),
    ("grid", "🧱  Сетка"),
    ("templates", "🖼  Шаблоны"),
    ("discord", "📨  Discord"),
]


class PreviewLabel(QLabel):
    """Кадр предпоказа (numpy BGR) с сохранением пропорций."""

    EMPTY_TEXT = "Откройте сундук в игре — здесь появится предпоказ."

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 190)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            f"background: {PALETTE['PANEL_2']}; border: 1px solid "
            f"{PALETTE['BORDER']}; border-radius: 8px; padding: 2px;")
        self.setText(self.EMPTY_TEXT)
        self._pixmap = None

    def set_frame(self, img, message=None):
        if img is None or getattr(img, "size", 0) == 0:
            self.clear_frame(message or self.EMPTY_TEXT)
            return
        h, w = img.shape[:2]
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        self._pixmap = QPixmap.fromImage(qimg)
        self.setText("")
        self._apply_scale()

    def clear_frame(self, message=None):
        self._pixmap = None
        self.setPixmap(QPixmap())
        self.setText(message or self.EMPTY_TEXT)

    def _apply_scale(self):
        if self._pixmap is None:
            return
        self.setPixmap(self._pixmap.scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_scale()


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
        self.autosave_lbl = make_label(
            "Изменения сохраняются автоматически.", muted=True, small=True)
        bottom.addWidget(self.autosave_lbl)
        bottom.addStretch(1)
        close_btn = HoverButton("Закрыть")
        close_btn.clicked.connect(self.reject)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

        self._pending_apply = False
        self._apply_tries = 0
        self._closing = False
        self._tour = None
        self._init_vars()
        self._build_pages()
        self._connect_autosave()

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
        v["capture_source"] = QComboBox()
        v["capture_source"].addItem("Весь экран", "screen")
        v["capture_source"].addItem("Окно игры", "window")
        v["capture_source"].setCurrentIndex(max(0, v["capture_source"].findData(
            cfg.get("capture_source", paths.DEFAULT_CONFIG["capture_source"]))))
        v["game_window_title"] = QLineEdit(str(cfg.get(
            "game_window_title", paths.DEFAULT_CONFIG["game_window_title"])))
        v["game_window_title"].setPlaceholderText("Minecraft")
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
        is_grid = 0 <= row < len(NAV_ITEMS) and NAV_ITEMS[row][0] == "grid"
        # Не оставляем таймер предпоказа работать при уходе со страницы сетки.
        if not is_grid:
            for name in ("_roi_timer", "_roi_debounce_timer"):
                timer = getattr(self, name, None)
                if timer is not None:
                    timer.stop()
        if 0 <= row < self.stack.count():
            self.stack.setCurrentIndex(row)
        if is_grid and not getattr(self, "_closing", False):
            self._start_live_roi_preview()

    # ── Обучающий тур с настройкой ──
    def start_tour(self):
        """Тур по настройкам: показывает разделы и требует настроить программу.

        Подсвеченная карточка не перекрыта затемнением, поэтому поля и кнопки
        в ней живые: пользователь настраивает программу прямо во время тура.
        Пока условие шага не выполнено, «Далее» и «Пропустить» заблокированы.
        """
        try:
            from chest_scanner.gui.tour import TourOverlay, TourStep
        except Exception as e:
            self._set_status(f"⚠ Тур недоступен: {e}", PALETTE["YELLOW"])
            return
        if getattr(self, "_tour", None) is not None:
            return
        self.nav.setCurrentRow(0)
        steps = [
            TourStep(
                "Настройка Chest Scanner",
                "Проведём по настройкам и сразу всё настроим. Поля можно менять "
                "прямо в туре — изменения сохраняются автоматически. Пока шаг "
                "не выполнен, кнопка «Далее» заблокирована."),
            TourStep(
                "1. Захват изображения",
                "Выберите, что снимать: весь экран или окно игры. Для окна "
                "укажите заголовок окна (например, Minecraft).",
                target=self._tour_card("Захват изображения"),
                before=lambda: self._tour_page("general", "Захват изображения"),
                require=self._tour_capture_ok,
                hint="Выберите источник захвата; для окна игры заполните заголовок."),
            TourStep(
                "2. Сетка сундука",
                "Откройте сундук в игре и нажмите «⚙ Автокалибровка» — программа "
                "сама найдёт сетку по снимку экрана и подставит координаты. "
                "Вручную можно через «Калибровка кликами» или ввести координаты.",
                target=self._tour_card("Область сундука (два угла)"),
                before=lambda: self._tour_page("grid",
                                               "Область сундука (два угла)"),
                require=self._tour_grid_ok,
                hint="Сетка не задана: нажмите «⚙ Автокалибровка» или введите "
                     "координаты и размеры.",
                action=("⚙ Автокалибровка", self._tour_autocalibrate)),
            TourStep(
                "3. Шаблоны",
                "Шаблон сундука и пустого слота нужны для распознавания. При "
                "открытом сундуке проще всего нажать «Создать недостающие».",
                target=self._tour_card("Автогенерация"),
                before=lambda: self._tour_page("templates", "Автогенерация"),
                require=self._tour_templates_ok,
                hint="Нужны оба шаблона: нажмите «Создать недостающие» или "
                     "выберите файлы вручную."),
            TourStep(
                "4. Распознавание",
                "Пороги SSIM и допуски pHash обычно менять не нужно — "
                "стандартные значения подходят большинству разрешений.",
                target=self._tour_card("Пороги распознавания (SSIM)"),
                before=lambda: self._tour_page("detection",
                                               "Пороги распознавания (SSIM)")),
            TourStep(
                "5. Discord (необязательно)",
                "Нужны отчёты в Discord — вставьте URL webhook канала. Без него "
                "всё остальное работает как обычно.",
                target=self._tour_card("Webhook"),
                before=lambda: self._tour_page("discord", "Webhook")),
            TourStep(
                "Готово",
                "Сетка, шаблоны и захват настроены. Нажмите «Готово» — настройки "
                "сохранятся и сразу применятся.",
                before=lambda: self._tour_page("general", "Захват изображения")),
        ]
        self._tour = TourOverlay(self, steps, on_finish=self._tour_finished)

    def _tour_finished(self):
        """Тур закрыт: сохраняем то, что пользователь настроил по ходу."""
        self._tour = None
        self._flush_autosave()
        try:
            if self._save():
                self._set_status("✓ Настройка завершена.", PALETTE["GREEN"])
        except Exception as e:
            self._set_status(f"⚠ Не сохранено: {e}", PALETTE["YELLOW"])

    def _tour_page(self, key, title=None):
        """Переключает раздел и прокручивает его к нужной карточке."""
        for row, (k, _t) in enumerate(NAV_ITEMS):
            if k == key:
                self.nav.setCurrentRow(row)
                self._show_page(row)
                break
        if not title:
            return
        card = self.findChild(QWidget, title)
        page = self.stack.currentWidget()
        area = page.findChild(QScrollArea) if page is not None else None
        if card is not None and area is not None:
            area.ensureWidgetVisible(card, 0, 12)

    def _tour_card(self, title):
        """Карточка настроек по заголовку (цель подсветки в туре)."""
        card = self.findChild(QWidget, title)
        return card if card is not None else self.stack.currentWidget()

    def _tour_capture_ok(self):
        if self._vars["capture_source"].currentData() == CAPTURE_SOURCE_WINDOW:
            return bool(self._vars["game_window_title"].text().strip())
        return True

    def _tour_grid_ok(self):
        grid = self._grid_from_fields()
        if not grid:
            return False
        return (grid["x2"] > grid["x1"] and grid["y2"] > grid["y1"]
                and grid["columns"] > 0 and grid["rows"] > 0)

    def _tour_templates_ok(self):
        for key in ("chest_template", "empty_template"):
            path = absolute_path(str(self._vars[key].value() or ""))
            if not path or not os.path.isfile(path):
                return False
        return True

    def _tour_autocalibrate(self):
        """Автокалибровка из тура: находит сетку по снимку и заполняет поля.

        Окна программы на время снимка скрываются, иначе они закрывают сундук
        в кадре. Возвращает текст для карточки шага.
        """
        try:
            self._flush_autosave()
            self._apply_runtime()
        except Exception as e:
            return f"Не удалось применить настройки: {e}"
        main = getattr(self, "main", None)
        main_shown = bool(main is not None and main.isVisible())
        result = None
        try:
            if main_shown:
                main.hide()
            else:
                self.hide()
            QApplication.processEvents()
            flush_compositor()
            time.sleep(0.25)
            result = self.reader.auto_detect_grid()
        except Exception as e:
            self._set_status(f"⚠ Автокалибровка: {e}", PALETTE["YELLOW"])
        finally:
            if main_shown:
                main.show()
                main.raise_()
            self.show()
            self.raise_()
            QApplication.processEvents()
            flush_compositor()
        if not result:
            self._set_status("⚠ Сетка не найдена.", PALETTE["YELLOW"])
            return ("Сетка не найдена: откройте сундук так, чтобы он был виден "
                    "целиком, и нажмите «⚙ Автокалибровка» ещё раз.")
        fields = {"x1": "grid_x1", "y1": "grid_y1", "x2": "grid_x2",
                  "y2": "grid_y2", "columns": "grid_cols", "rows": "grid_rows"}
        for key, field in fields.items():
            if key in result:
                self._vars[field].setText(str(result[key]))
        try:
            self.reader.reload_profile()
        except Exception:
            # Профиль перечитается при закрытии окна — здесь это не критично.
            pass
        self._flush_autosave()
        self._set_status("✓ Сетка определена автоматически.", PALETTE["GREEN"])
        return (f"Сетка найдена: ({result['x1']},{result['y1']}) → "
                f"({result['x2']},{result['y2']}), "
                f"{result['columns']}×{result['rows']} ячеек.")

    # ── Живой предпоказ областей иконки и цифр ──
    def _start_live_roi_preview(self):
        """Запускает периодическое обновление предпоказа только на странице сетки."""
        timer = getattr(self, "_roi_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setInterval(1000)
            timer.timeout.connect(self._refresh_roi_preview)
            self._roi_timer = timer
        if not timer.isActive():
            timer.start(1000)
        # Не ждём первый интервал: показываем актуальный кадр сразу при входе.
        self._schedule_roi_preview(0)

    def _schedule_roi_preview(self, *_args):
        """Короткая задержка после правки полей плюс независимое автообновление."""
        if getattr(self, "_closing", False):
            return
        timer = getattr(self, "_roi_debounce_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._refresh_roi_preview)
            self._roi_debounce_timer = timer
        timer.start(250)

    def _read_roi_fields(self, prefix):
        """Четыре числа области из полей или None, если введено не число."""
        try:
            values = [float(str(self._vars[f"{prefix}_{i}"].text()).strip())
                      for i in range(4)]
        except ValueError:
            return None
        return values

    def _grid_from_fields(self):
        """Сетка из полей (до сохранения) либо None, если поля неполные."""
        try:
            return {"x1": int(float(self._vars["grid_x1"].text())),
                    "y1": int(float(self._vars["grid_y1"].text())),
                    "x2": int(float(self._vars["grid_x2"].text())),
                    "y2": int(float(self._vars["grid_y2"].text())),
                    "columns": int(float(self._vars["grid_cols"].text())),
                    "rows": int(float(self._vars["grid_rows"].text()))}
        except ValueError:
            return None

    def _capture_for_preview(self):
        """Снимок для предпоказа без скрытия/повторного показа диалога.

        Для захвата всего экрана используем кадр напрямую: кратковременное
        hide/show модального окна приводило к мерцанию и нестабильности Qt.
        В режиме окна игры захватывается только окно Minecraft.
        """
        return screenshot_bgr()

    def _refresh_roi_preview(self):
        if (not hasattr(self, "roi_preview") or not self.isVisible()
                or getattr(self, "_closing", False)
                or self.stack.currentIndex() >= len(NAV_ITEMS)
                or NAV_ITEMS[self.stack.currentIndex()][0] != "grid"):
            return
        if getattr(self, "_roi_busy", False):
            return
        self._roi_busy = True
        try:
            icon_roi = self._read_roi_fields("icon")
            count_roi = self._read_roi_fields("count")
            if icon_roi is None or count_roi is None:
                self.roi_preview.clear_frame(
                    "Введите четыре числа в областях иконки и цифр.")
                self.roi_status.setText("")
                return
            grid = self._grid_from_fields()
            if grid is None:
                self.roi_preview.clear_frame(
                    "Проверьте границы и размер сетки (X1…Y2, столбцы, строки).")
                self.roi_status.setText("")
                return
            try:
                screen = self._capture_for_preview()
                report = self.reader.diagnose_roi_settings(
                    screen=screen, icon_roi=icon_roi, count_roi=count_roi,
                    grid=grid)
            except Exception as e:
                self.roi_preview.clear_frame(f"Предпоказ недоступен: {e}")
                self.roi_status.setText("")
                return
            issues = report.get("issues") or []
            self.roi_preview.set_frame(report.get("overlay"),
                                       issues[0] if issues else None)
            slots = report.get("slots") or []
            if slots:
                sample = slots[0]
                self.icon_crop_preview.set_frame(sample.get("icon"),
                                                 "Иконка недоступна")
                self.count_crop_preview.set_frame(sample.get("count"),
                                                  "Область цифр недоступна")
                binary = sample.get("binary")
                if binary is not None and getattr(binary, "size", 0):
                    binary_bgr = cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)
                    self.count_mask_preview.set_frame(binary_bgr)
                else:
                    self.count_mask_preview.clear_frame("Маска не построена")
                qtys = ", ".join(
                    f"r{s['row']}c{s['column']}={s['quantity']}" for s in slots)
                self.roi_status.setText(
                    f"Вырезки показаны для слота r{sample['row']}c{sample['column']}. "
                    f"Количество: {qtys}")
            else:
                for crop_preview in (self.icon_crop_preview,
                                     self.count_crop_preview,
                                     self.count_mask_preview):
                    crop_preview.clear_frame("Нет заполненного слота")
                self.roi_status.setText("Заполненных слотов не видно.")
        finally:
            self._roi_busy = False

    def _page_general(self, lay, v):
        card_src = SettingCard("Захват изображения",
                               "Что снимать для сканирования: весь экран или окно игры.")
        row_src = QHBoxLayout()
        row_src.setSpacing(8)
        row_src.addWidget(make_label("Источник", muted=True))
        v["capture_source"].setFixedWidth(170)
        row_src.addWidget(v["capture_source"])
        row_src.addStretch(1)
        card_src.content.addLayout(row_src)
        row_title = QHBoxLayout()
        row_title.setSpacing(8)
        row_title.addWidget(make_label("Заголовок окна", muted=True))
        v["game_window_title"].setFixedWidth(220)
        row_title.addWidget(v["game_window_title"])
        row_title.addStretch(1)
        card_src.content.addLayout(row_title)
        lay.addWidget(card_src)

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

        prev_card = SettingCard(
            "Живой предпоказ",
            "Обновляется при изменении области. Белая рамка — слот, "
            "зелёная — иконка, жёлтая — цифры.")
        self.roi_preview = PreviewLabel()
        prev_card.content.addWidget(self.roi_preview)

        # Показываем реальные вырезанные области первого найденного предмета.
        # Это позволяет проверить не только рамки на сетке, но и сами пиксели,
        # которые затем поступают в распознавание иконки и количества.
        crops_row = QHBoxLayout()
        crops_row.setSpacing(10)
        self.icon_crop_preview = PreviewLabel()
        self.count_crop_preview = PreviewLabel()
        self.count_mask_preview = PreviewLabel()
        for crop_preview in (self.icon_crop_preview, self.count_crop_preview,
                             self.count_mask_preview):
            crop_preview.setMinimumSize(120, 90)
            crop_preview.setMaximumHeight(112)
            crops_row.addWidget(crop_preview, 1)
        crops_card = SettingCard(
            "Фактические вырезки первого заполненного слота",
            "Слева — иконка, по центру — область количества, справа — "
            "бинарная маска цифр после обработки.")
        crops_card.content.addLayout(crops_row)
        prev_card.content.addWidget(crops_card)

        self.roi_status = make_label("", muted=True, small=True, wrap=True)
        prev_card.content.addWidget(self.roi_status)
        lay.addWidget(prev_card)

        for i in range(4):
            v[f"icon_{i}"].textChanged.connect(self._schedule_roi_preview)
            v[f"count_{i}"].textChanged.connect(self._schedule_roi_preview)
        for key in ("grid_x1", "grid_y1", "grid_x2", "grid_y2",
                    "grid_cols", "grid_rows"):
            v[key].textChanged.connect(self._schedule_roi_preview)

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

    # ── Автосохранение ──
    def _connect_autosave(self):
        """Любая правка поля запускает отложенное сохранение в конфиг."""
        v = self._vars
        for key in ("ref_w", "ref_h", "game_window_title", "webhook",
                    "discord_env", "max_items", "stack_size"):
            v[key].textChanged.connect(self._schedule_autosave)
        for i in range(4):
            v[f"icon_{i}"].textChanged.connect(self._schedule_autosave)
            v[f"count_{i}"].textChanged.connect(self._schedule_autosave)
        for key in ("grid_x1", "grid_y1", "grid_x2", "grid_y2",
                    "grid_cols", "grid_rows"):
            v[key].textChanged.connect(self._schedule_autosave)
        for key in ("print_candidates", "auto_scan", "item_detect_cache",
                    "tray_minimize", "notify_scan", "delete_old"):
            v[key].toggled.connect(self._schedule_autosave)
        for key in ("scan_interval", "chest_threshold", "empty_threshold",
                    "item_threshold", "item_margin", "phash_threshold"):
            v[key]._slider.valueChanged.connect(self._schedule_autosave)
        v["capture_source"].currentIndexChanged.connect(self._schedule_autosave)
        v["chest_template"].changed.connect(self._schedule_autosave)
        v["empty_template"].changed.connect(self._schedule_autosave)

    def _schedule_autosave(self, *_args):
        timer = getattr(self, "_autosave_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._autosave)
            self._autosave_timer = timer
        timer.start(600)

    def _autosave(self):
        if getattr(self, "_closing", False) or not self.isVisible():
            return
        try:
            if self._save():
                self._set_status("✓ Сохранено", PALETTE["GREEN"])
        except Exception as e:  # никакая ошибка не должна валить GUI-поток
            self._set_status(f"⚠ Ошибка сохранения: {e}", PALETTE["YELLOW"])

    def _set_status(self, text, color=None):
        self.autosave_lbl.setText(text)
        self.autosave_lbl.setStyleSheet(f"color: {color};" if color else "")

    def _flush_autosave(self):
        """Сохраняет отложенные изменения перед закрытием окна."""
        timer = getattr(self, "_autosave_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()
            self._autosave()

    def done(self, result):
        if getattr(self, "_closing", False):
            return
        self._closing = True

        # Остановить дочерние таймеры ДО закрытия/уничтожения QWidget.
        # Иначе уже поставленный timeout может попасть в Qt event loop
        # одновременно с разрушением дочерних QLabel/QPixmap.
        for name in ("_roi_timer", "_roi_debounce_timer", "_autosave_timer"):
            timer = getattr(self, name, None)
            if timer is not None:
                try:
                    timer.stop()
                except RuntimeError:
                    pass

        try:
            # Сохраняем напрямую: _autosave намеренно не работает при closing.
            self._save()
        except Exception as e:
            try:
                self.main._log(f"Ошибка сохранения настроек: {e}", PALETTE["RED"])
            except Exception:
                pass

        pending = bool(self._pending_apply)
        main = self.main
        super().done(result)

        # Callback принадлежит главному окну, а не закрываемому диалогу.
        if pending and main is not None:
            QTimer.singleShot(0, main, lambda m=main: m._apply_settings_runtime())

    def _apply_runtime(self):
        """Применяет сохранённый конфиг к сканеру и главному окну."""
        if not self._pending_apply:
            return
        main = self.main
        tries = getattr(self, "_apply_tries", 0)
        if getattr(main, "busy", False) and tries < 20:
            # Не трогаем шаблоны и сетку, пока идёт скан.
            self._apply_tries = tries + 1
            QTimer.singleShot(500, self._apply_runtime)
            return
        self._apply_tries = 0
        self._pending_apply = False
        try:
            was_auto = main.reader.auto_scan_active
            if was_auto:
                main.reader.stop_auto_scan()
            main.reader.reload_profile()
            # Запущенный автоскан не должен гаснуть от правки настроек.
            if was_auto or main.reader.config.get("auto_scan_enabled", False):
                main.reader.start_auto_scan()
            main._sync_auto_scan_btn()
            main._refresh_profile_label()
            main._refresh_stats()
            try:
                main.hotkeys_hint_label.setText(main._hotkeys_hint())
            except Exception:
                pass
            main._refresh_hotkeys()
        except Exception as e:
            try:
                main._log(f"Ошибка применения настроек: {e}", PALETTE["RED"])
            except Exception:
                pass

    # ── Действия из настроек ──
    def _safe(self, func):
        try:
            # Действия (калибровка, проверка сетки) должны работать с текущими
            # значениями из полей, поэтому сначала сохраняем и применяем их.
            self._flush_autosave()
            self._apply_runtime()
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
        """Записывает поля в файл конфига. Возвращает True при успехе.

        Не трогает работающий сканер: тяжёлое применение (перечитать шаблоны,
        перезапустить автоскан/хоткеи) делает `_apply_runtime()` при закрытии
        окна, чтобы автосохранение не гоняло его на каждую правку.
        """
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
            self._save_error(str(e))
            return False

        webhook = v["webhook"].text().strip()
        if webhook and not webhook.startswith(("https://discord.com/api/webhooks/",
                                               "https://discordapp.com/api/webhooks/")):
            self._save_error("Некорректный Webhook URL.")
            return False

        cfg = self.reader.config
        cfg["capture_source"] = v["capture_source"].currentData() or "screen"
        cfg["game_window_title"] = (v["game_window_title"].text().strip()
                                    or paths.DEFAULT_CONFIG["game_window_title"])
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
        self._pending_apply = True
        return True

    def _save_error(self, message):
        """Ошибка ввода: подсказка в статусе вместо модального окна."""
        self._set_status(f"⚠ Не сохранено: {message}", PALETTE["YELLOW"])
