"""GUI dialog: все диалоговые окна приложения на PySide6."""
import json
import os
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QIntValidator, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from chest_scanner import paths, storage
from chest_scanner.commons import load_json, results_history_guard, save_json
from chest_scanner.config import (
    activate_profile,
    create_profile_backup,
    create_profile_data,
    delete_profile_data,
    export_profile_archive,
    import_profile_archive,
    list_profile_backups,
    load_profiles,
    rename_profile_data,
    restore_profile_backup,
    save_profiles,
)
from chest_scanner.export import (
    export_chests_to_csv,
    export_chests_to_json,
    export_summary_to_csv,
)
from chest_scanner.gui.item_db_view import ItemDatabaseView
from chest_scanner.gui.theme import PALETTE
from chest_scanner.gui.widgets import HoverButton, StatCard, make_label
from chest_scanner.history_restore import apply_history_restore, prepare_history_restore
from chest_scanner.history_utils import history_sort_key, parse_history_timestamp
from chest_scanner.logging_setup import logger


def _question(parent, title, text, default_no=False):
    btn = QMessageBox.Yes
    if default_no:
        btn = QMessageBox.No
    ret = QMessageBox.question(parent, title, text, btn | QMessageBox.Cancel)
    return ret == QMessageBox.Yes


# ── Мастер первого запуска ──────────────────────────────────────────
class FirstRunWizard(QDialog):
    """Трёхшаговый мастер: профиль → Discord → готово."""

    def __init__(self, reader, parent=None):
        super().__init__(parent)
        self.reader = reader
        self.setWindowTitle("Первый запуск")
        self.resize(720, 500)
        self.setMinimumSize(660, 440)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(6)

        title = make_label("Настройка Chest Scanner", title=True)
        root.addWidget(title)
        subtitle = make_label("Выберите профиль и настройте Discord.", muted=True)
        root.addWidget(subtitle)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self.profile_edit = QLineEdit(load_profiles().get("active", "Default"))
        self.webhook_edit = QLineEdit(self.reader.get_discord_webhook_url() or "")

        page1 = QWidget()
        p1 = QVBoxLayout(page1)
        p1.setContentsMargins(0, 18, 0, 0)
        p1.setSpacing(10)
        p1.addWidget(make_label("1. Профиль", color=PALETTE["ACCENT_HOVER"]))
        p1.addWidget(make_label(
            "Имя профиля для отдельной базы и результатов.", muted=True))
        p1.addWidget(self.profile_edit)
        p1.addStretch(1)
        self.stack.addWidget(page1)

        page2 = QWidget()
        p2 = QVBoxLayout(page2)
        p2.setContentsMargins(0, 18, 0, 0)
        p2.setSpacing(10)
        p2.addWidget(make_label("2. Discord Webhook", color=PALETTE["ACCENT_HOVER"]))
        p2.addWidget(make_label(
            "Можно настроить позже через «Настройки».", muted=True))
        self.webhook_edit.setPlaceholderText("https://discord.com/api/webhooks/...")
        p2.addWidget(self.webhook_edit)
        p2.addStretch(1)
        self.stack.addWidget(page2)

        page3 = QWidget()
        p3 = QVBoxLayout(page3)
        p3.setContentsMargins(0, 18, 0, 0)
        p3.setSpacing(10)
        p3.addWidget(make_label("3. Готово", color=PALETTE["GREEN"]))
        p3.addWidget(make_label(
            "Настройки сохранены.\nОткройте сундук и нажмите F8.", muted=True))
        p3.addStretch(1)
        self.stack.addWidget(page3)

        nav = QHBoxLayout()
        nav.setSpacing(8)
        self.back_btn = HoverButton("Назад")
        self.back_btn.clicked.connect(self._go_back)
        nav.addWidget(self.back_btn)
        nav.addStretch(1)
        self.next_btn = HoverButton("Далее", accent=True)
        self.next_btn.clicked.connect(self._go_next)
        nav.addWidget(self.next_btn)
        root.addLayout(nav)

        self._page = 0
        self._render()

    def _render(self):
        self.stack.setCurrentIndex(self._page)
        self.back_btn.setEnabled(self._page > 0)
        self.next_btn.setText("Готово" if self._page == 2 else "Далее")

    def _go_back(self):
        if self._page > 0:
            self._page -= 1
            self._render()

    def _go_next(self):
        if self._page < 2:
            self._page += 1
            self._render()
            return
        self._finish()

    def _finish(self):
        reader = self.reader
        name = self.profile_edit.text().strip() or "Default"
        if any(c in name for c in '/\\:*?"<>|'):
            QMessageBox.critical(self, "Профиль", "Недопустимые символы.")
            return
        wh = self.webhook_edit.text().strip()
        if wh and not wh.startswith(("https://discord.com/api/webhooks/",
                                     "https://discordapp.com/api/webhooks/")):
            QMessageBox.critical(self, "Discord", "Некорректный Webhook URL.")
            return
        data = load_profiles()
        existing = next((entry.get("name") for entry in data.get("profiles", [])
                         if str(entry.get("name", "")).casefold() == name.casefold()), None)
        if existing:
            name = existing
        else:
            # Use the journaled creation path; activation itself is read-only
            # with respect to the profile directory and cannot recreate it.
            create_profile_data(name)
        activate_profile(name)
        reader.reload_profile()
        if wh:
            save_json(paths.DISCORD_WEBHOOK_FILE, {"webhook_url": wh})
        reader.config["profile_name"] = name
        reader.config["setup_complete"] = True
        save_json(paths.CONFIG_FILE, reader.config)
        data = load_profiles()
        data["active"] = name
        data["setup_complete"] = True
        save_profiles(data)
        self.accept()


# ── Неизвестный предмет ─────────────────────────────────────────────
class UnknownItemDialog(QDialog):
    """Промпт для нового предмета: имя + категория."""

    def __init__(self, unknown_name, image_path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Новый предмет")
        self.resize(680, 380)
        self.setMinimumSize(620, 340)
        self.category_key = "uncategorized"

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(8)

        root.addWidget(make_label("Найден новый предмет", title=True))
        body = QHBoxLayout()
        body.setSpacing(20)

        self.icon_lbl = QLabel("Нет\nиконки")
        self.icon_lbl.setProperty("muted", True)
        self.icon_lbl.setAlignment(Qt.AlignCenter)
        self.icon_lbl.setFixedSize(140, 140)
        self.icon_lbl.setStyleSheet(
            f"background: {PALETTE['PANEL']}; border: 1px solid {PALETTE['BORDER']};"
            " border-radius: 10px;")
        body.addWidget(self.icon_lbl, 0, Qt.AlignTop)
        self._load_icon(image_path)

        info = QVBoxLayout()
        info.setSpacing(10)
        info.addWidget(make_label(f"Шаблон: {unknown_name}", muted=True, small=True))
        info.addWidget(make_label("Название предмета:"))
        self.name_edit = QLineEdit()
        self.name_edit.setFocus()
        info.addWidget(self.name_edit)
        info.addWidget(make_label("Категория:"))
        self.cat_combo = QComboBox()
        for key, title in paths.ITEM_CATEGORIES:
            self.cat_combo.addItem(title, key)
        info.addWidget(self.cat_combo)
        info.addStretch(1)
        body.addLayout(info, 1)
        root.addLayout(body, 1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        skip = HoverButton("Оставить unknown")
        skip.clicked.connect(self.reject)
        btns.addWidget(skip)
        self.add_btn = HoverButton("Добавить в базу", accent=True)
        self.add_btn.setEnabled(False)
        self.add_btn.clicked.connect(self.accept)
        self.name_edit.textChanged.connect(
            lambda text: self.add_btn.setEnabled(bool(text.strip())))
        btns.addWidget(self.add_btn)
        root.addLayout(btns)

        self.name_edit.returnPressed.connect(self.accept)

    def _load_icon(self, image_path):
        try:
            if image_path and os.path.isfile(image_path):
                pm = QPixmap(image_path)
                if not pm.isNull():
                    pm = pm.scaled(132, 132, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self.icon_lbl.setPixmap(pm)
        except Exception:
            pass

    def accept(self):
        if not self.item_name():
            self.name_edit.setFocus()
            return
        idx = self.cat_combo.currentIndex()
        if idx >= 0:
            self.category_key = self.cat_combo.itemData(idx)
        super().accept()

    def item_name(self):
        return self.name_edit.text().strip()


# ── История ─────────────────────────────────────────────────────────
def load_all_chests(strict=False):
    """Load current and archived scans; strict mode rejects incomplete history."""
    chests = []
    seen_ids = set()

    def append_unique(entries, source=None):
        if not isinstance(entries, list):
            if strict:
                raise ValueError(f"Некорректный список сканирований: {source or paths.RESULTS_FILE}")
            return
        for index, chest in enumerate(entries):
            if not isinstance(chest, dict):
                if strict:
                    raise ValueError(
                        f"Некорректная запись сканирования #{index + 1}: {source or paths.RESULTS_FILE}")
                continue
            for field, expected in (("slots", list), ("totals", dict), ("unknown_items", list)):
                if field in chest and not isinstance(chest[field], expected):
                    if strict:
                        raise ValueError(
                            f"Некорректное поле {field} в {source or paths.RESULTS_FILE}, запись #{index + 1}")
                    break
            else:
                chest_id = chest.get("id")
                if chest_id is not None:
                    key = str(chest_id)
                    if key in seen_ids:
                        continue
                    seen_ids.add(key)
                entry = dict(chest)
                if source:
                    entry["_source"] = source
                chests.append(entry)
                continue
            continue

    if strict:
        if os.path.exists(paths.RESULTS_FILE):
            try:
                with open(paths.RESULTS_FILE, "r", encoding="utf-8") as stream:
                    data = json.load(stream)
            except (json.JSONDecodeError, OSError, UnicodeError) as exc:
                raise ValueError(f"Не удалось прочитать текущую историю: {exc}") from exc
            if not isinstance(data, dict) or not isinstance(data.get("chests"), list):
                raise ValueError("Текущая история имеет неверный формат (ожидается объект с chests).")
            append_unique(data["chests"])
    else:
        data = load_json(paths.RESULTS_FILE, {"chests": []})
        if isinstance(data, dict):
            append_unique(data.get("chests", []))

    if os.path.isdir(paths.HISTORY_DIR):
        for fn in sorted(os.listdir(paths.HISTORY_DIR)):
            if not fn.endswith(".json"):
                continue
            fp = os.path.join(paths.HISTORY_DIR, fn)
            try:
                with open(fp, "r", encoding="utf-8") as stream:
                    archived = json.load(stream)
                if isinstance(archived, dict):
                    entries = archived.get("chests")
                elif isinstance(archived, list) and not strict:
                    entries = archived
                else:
                    entries = None
                if strict and (not isinstance(archived, dict)
                               or not isinstance(archived.get("chests"), list)):
                    raise ValueError("ожидается объект с массивом chests")
                append_unique(entries, fn)
            except (json.JSONDecodeError, OSError, UnicodeError, ValueError) as exc:
                if strict:
                    raise ValueError(f"Архив истории повреждён ({fn}): {exc}") from exc
                continue
    chests.sort(key=lambda chest: history_sort_key(chest.get("timestamp", "")), reverse=True)
    return chests


def _load_history_stack_sizes():
    """Return valid per-item stack sizes from the active item database."""
    data = load_json(paths.ITEMS_FILE, {"items": []})
    items = data.get("items", []) if isinstance(data, dict) else []
    if not isinstance(items, list):
        return {}
    result = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        name = item["name"].strip()
        if not name:
            continue
        try:
            result[name] = max(1, int(item.get("stack_size", 64)))
        except (TypeError, ValueError):
            result[name] = 64
    return result


class HistoryDialog(QDialog):
    """История сканирований: фильтры, детали, сравнение."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("История сканирований")
        self.resize(1100, 700)
        self.setMinimumSize(820, 520)
        self._all_chests = load_all_chests()
        self._stack_sizes = _load_history_stack_sizes()
        self._filtered = []

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)
        root.addWidget(make_label("История сканирований", title=True))

        # Фильтры
        ff = QFrame()
        ff.setProperty("card", True)
        fr = QGridLayout(ff)
        fr.setContentsMargins(14, 10, 14, 10)
        fr.setHorizontalSpacing(8)
        fr.setVerticalSpacing(6)
        fr.addWidget(make_label("\u041f\u0440\u0435\u0434\u043c\u0435\u0442:", muted=True, small=True), 0, 0)
        self.item_edit = QLineEdit()
        self.item_edit.setMinimumWidth(70)
        self.item_edit.setMaximumWidth(180)
        fr.addWidget(self.item_edit, 0, 1)
        fr.addWidget(make_label("\u041c\u0438\u043d. \u043a\u043e\u043b-\u0432\u043e:", muted=True, small=True), 0, 2)
        self.qty_edit = QLineEdit()
        self.qty_edit.setValidator(QIntValidator(1, 2_147_483_647, self))
        self.qty_edit.setPlaceholderText("\u041c\u0438\u043d.\u043a\u043e\u043b-\u0432\u043e")
        self.qty_edit.setToolTip("\u041f\u043e\u043a\u0430\u0437\u0430\u0442\u044c \u0441\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u044f, \u0433\u0434\u0435 \u0441\u0443\u043c\u043c\u0430 \u043f\u0440\u0435\u0434\u043c\u0435\u0442\u043e\u0432 \u043d\u0435 \u043c\u0435\u043d\u044c\u0448\u0435 \u0443\u043a\u0430\u0437\u0430\u043d\u043d\u043e\u0433\u043e")
        self.qty_edit.setMinimumWidth(45)
        self.qty_edit.setMaximumWidth(70)
        fr.addWidget(self.qty_edit, 0, 3)
        fr.addWidget(make_label("\u0414\u0430\u0442\u0430 \u043e\u0442:", muted=True, small=True), 1, 0)
        self.from_edit = QLineEdit()
        self.from_edit.setMinimumWidth(70)
        self.from_edit.setMaximumWidth(100)
        self.from_edit.setPlaceholderText("\u0414\u0414.\u041c\u041c.\u0413\u0413\u0413\u0413")
        fr.addWidget(self.from_edit, 1, 1)
        fr.addWidget(make_label("\u0414\u0430\u0442\u0430 \u0434\u043e:", muted=True, small=True), 1, 2)
        self.to_edit = QLineEdit()
        self.to_edit.setToolTip("\u041f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0439 \u0434\u0435\u043d\u044c \u0432\u043a\u043b\u044e\u0447\u0438\u0442\u0435\u043b\u044c\u043d\u043e")
        self.to_edit.setMinimumWidth(70)
        self.to_edit.setMaximumWidth(100)
        self.to_edit.setPlaceholderText("\u0414\u0414.\u041c\u041c.\u0413\u0413\u0413\u0413")
        fr.addWidget(self.to_edit, 1, 3)
        apply_btn = HoverButton("\u041f\u0440\u0438\u043c\u0435\u043d\u0438\u0442\u044c")
        apply_btn.clicked.connect(self._refresh_list)
        fr.addWidget(apply_btn, 0, 4)
        reset_btn = HoverButton("\u0421\u0431\u0440\u043e\u0441\u0438\u0442\u044c")
        reset_btn.clicked.connect(self._clear_filters)
        fr.addWidget(reset_btn, 1, 4)
        fr.setColumnStretch(1, 1)
        fr.setColumnStretch(3, 1)
        for field in (self.item_edit, self.qty_edit, self.from_edit, self.to_edit):
            field.returnPressed.connect(self._refresh_list)
        root.addWidget(ff)

        # Контент
        content = QHBoxLayout()
        content.setSpacing(10)
        root.addLayout(content, 1)

        left_wrap = QFrame()
        left_wrap.setProperty("card", True)
        lw = QVBoxLayout(left_wrap)
        lw.setContentsMargins(8, 10, 8, 8)
        lw.setSpacing(4)
        lw.addWidget(make_label("Сканирования", section=True))
        self.listbox = QListWidget()
        self.listbox.setSelectionMode(QListWidget.ExtendedSelection)
        self.listbox.currentItemChanged.connect(self._show_details)
        lw.addWidget(self.listbox, 1)
        left_wrap.setMinimumWidth(220)
        left_wrap.setMaximumWidth(400)
        content.addWidget(left_wrap)

        right_wrap = QFrame()
        right_wrap.setProperty("card", True)
        rw = QVBoxLayout(right_wrap)
        rw.setContentsMargins(16, 12, 16, 10)
        rw.setSpacing(6)
        self.detail_header = make_label("Выберите сканирование", muted=True)
        rw.addWidget(self.detail_header)
        self.detail_info = make_label("", muted=True)
        self.detail_info.setWordWrap(True)
        rw.addWidget(self.detail_info)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Предмет", "Кол-во", "Стаков", "Категория"])
        self.table.setColumnWidth(0, 240)
        self.table.setColumnWidth(1, 75)
        self.table.setColumnWidth(2, 65)
        self.table.setColumnWidth(3, 130)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        rw.addWidget(self.table, 1)
        content.addWidget(right_wrap, 1)

        # Низ
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.compare_btn = HoverButton("Сравнить выбранные")
        self.compare_btn.setToolTip("\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0434\u0432\u0430 \u0441\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u044f; \u0434\u043b\u044f \u0432\u044b\u0431\u043e\u0440\u0430 \u043d\u0435\u0441\u043a\u043e\u043b\u044c\u043a\u0438\u0445 \u0443\u0434\u0435\u0440\u0436\u0438\u0432\u0430\u0439\u0442\u0435 Ctrl")
        self.compare_btn.setEnabled(False)
        self.listbox.itemSelectionChanged.connect(self._update_compare_button)
        self.compare_btn.clicked.connect(self._compare)
        bottom.addWidget(self.compare_btn)
        bottom.addStretch(1)
        self.total_lbl = make_label(f"Всего сканирований: {len(self._all_chests)}", muted=True)
        bottom.addWidget(self.total_lbl)
        close_btn = HoverButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

        self._refresh_list()

    # ── Фильтры ──
    @staticmethod
    def _parse_date(text):
        text = text.strip()
        if not text:
            return None
        try:
            return datetime.strptime(text, "%d.%m.%Y")
        except ValueError:
            return None

    def _apply_filters(self, chests):
        item_f = self.item_edit.text().strip().lower()
        qty_f = 0
        try:
            qty_f = int(self.qty_edit.text().strip() or "0")
        except ValueError:
            pass
        raw_from = self.from_edit.text().strip()
        raw_to = self.to_edit.text().strip()
        d_from = self._parse_date(raw_from)
        d_to = self._parse_date(raw_to)
        self._filter_error = None
        if raw_from and d_from is None:
            self._filter_error = "Неверная дата «от»: используйте ДД.ММ.ГГГГ."
        elif raw_to and d_to is None:
            self._filter_error = "Неверная дата «до»: используйте ДД.ММ.ГГГГ."
        if self._filter_error:
            return []
        result = []
        for ch in chests:
            ts = ch.get("timestamp", "")
            totals = ch.get("totals", {})
            if item_f and not any(item_f in n.lower() for n in totals):
                continue
            if qty_f > 0 and sum(totals.values()) < qty_f:
                continue
            if d_from or d_to:
                try:
                    cd = datetime.strptime(ts, "%d.%m.%Y %H:%M:%S")
                except (ValueError, TypeError):
                    continue
                if d_from and cd < d_from:
                    continue
                if d_to and cd > d_to.replace(hour=23, minute=59, second=59):
                    continue
            result.append(ch)
        return result

    def _clear_filters(self):
        self.item_edit.clear()
        self.qty_edit.clear()
        self.from_edit.clear()
        self.to_edit.clear()
        self._refresh_list()

    def _refresh_list(self):
        self._filtered = self._apply_filters(self._all_chests)
        if self._filter_error:
            self.total_lbl.setText(self._filter_error)
            self.total_lbl.setStyleSheet(f"color: {PALETTE['RED']};")
        else:
            self.total_lbl.setText(f"Показано: {len(self._filtered)} из {len(self._all_chests)}")
            self.total_lbl.setStyleSheet("")
        self.listbox.clear()
        for ch in self._filtered:
            ts = ch.get("timestamp", "?")
            slots = ch.get("occupied_slots", 0)
            items_count = ch.get("unique_items", 0)
            src = ch.get("_source", "")
            label = f"{ts}  |  {slots} ячеек  |  {items_count} предм."
            if src:
                label += f"  [{src[:12]}]"
            self.listbox.addItem(label)
        self.detail_header.setText("Выберите сканирование")
        self.detail_info.setText("")
        self.table.setRowCount(0)

    def _current_chest(self):
        row = self.listbox.currentRow()
        if row < 0 or row >= len(self._filtered):
            return None
        return self._filtered[row]

    def _show_details(self, *_):
        ch = self._current_chest()
        if not ch:
            return
        ts = ch.get("timestamp", "?")
        self.detail_header.setText(f"Сканирование от {ts}")
        res = ch.get("screen_resolution", [0, 0])
        info = (f"Ячеек занято: {ch.get('occupied_slots', 0)}    "
                f"Уникальных: {ch.get('unique_items', 0)}    "
                f"Разрешение: {res[0]}x{res[1]}")
        unknowns = ch.get("unknown_items", [])
        if unknowns:
            info += f"\nНеизвестные: {', '.join(unknowns)}"
        qty_unknown = int(ch.get("quantity_unknown_slots", 0) or 0)
        if qty_unknown:
            info += f"\nКоличество не распознано в ячейках: {qty_unknown}"
        self.detail_info.setText(info)
        self.table.setRowCount(0)
        totals = ch.get("totals", {})
        categories = ch.get("categories", {})
        item_cats = {}
        for cat, items in categories.items():
            for item_name in items:
                item_cats[item_name] = cat
        for name, qty in sorted((totals or {}).items(), key=lambda x: str(x[0]).lower()):
            if qty is None:
                continue
            try:
                quantity = int(qty)
            except (TypeError, ValueError):
                continue
            stack_size = self._stack_sizes.get(name, 64)
            stacks = quantity // stack_size
            cat = item_cats.get(name, "uncategorized")
            cat_name = paths.CATEGORY_NAMES.get(cat, cat)
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 1, QTableWidgetItem(str(quantity)))
            self.table.setItem(row, 2, QTableWidgetItem(str(stacks)))
            self.table.setItem(row, 3, QTableWidgetItem(cat_name))
        for slot in (ch.get("slots") or []):
            if (not isinstance(slot, dict) or slot.get("quantity") is not None
                    or not slot.get("item")):
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            cat = slot.get("category", "uncategorized")
            self.table.setItem(row, 0, QTableWidgetItem(slot["item"]))
            self.table.setItem(row, 1, QTableWidgetItem("Не распознано"))
            self.table.setItem(row, 2, QTableWidgetItem("—"))
            self.table.setItem(row, 3, QTableWidgetItem(paths.CATEGORY_NAMES.get(cat, cat)))

    def _update_compare_button(self):
        """Enable comparison only after two scans have been selected."""
        self.compare_btn.setEnabled(len(self.listbox.selectedIndexes()) >= 2)

    def _compare(self):
        rows = self.listbox.selectedIndexes()
        if len(rows) < 2:
            QMessageBox.information(
                self, "\u0421\u0440\u0430\u0432\u043d\u0435\u043d\u0438\u0435",
                "\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0434\u0432\u0430 \u0441\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u044f.\n"
                "\u0423\u0434\u0435\u0440\u0436\u0438\u0432\u0430\u0439\u0442\u0435 Ctrl \u043f\u0440\u0438 \u043a\u043b\u0438\u043a\u0435.")
            return
        selected = [self._filtered[r.row()] for r in rows[:2]]
        selected.sort(key=_scan_sort_key)
        ComparisonDialog(selected[0], selected[1], self).exec()

# ── Сравнение ───────────────────────────────────────────────────────
def _scan_sort_key(chest):
    """\u0421\u043e\u0440\u0442\u0438\u0440\u0443\u0435\u0442 \u0437\u0430\u043f\u0438\u0441\u0438 \u043f\u043e \u0432\u0440\u0435\u043c\u0435\u043d\u0438 \u0438 \u0434\u0430\u0442\u0435."""
    parsed = parse_history_timestamp(chest.get("timestamp", ""))
    return (parsed is None, parsed or datetime.max)


def _comparison_snapshot(chest):
    """Return item totals and names whose complete quantity is unknown."""
    chest = chest if isinstance(chest, dict) else {}
    raw_totals = chest.get("totals")
    totals = raw_totals if isinstance(raw_totals, dict) else {}
    values = dict(totals)
    unknown = {name for name, quantity in totals.items() if quantity is None}
    slots = chest.get("slots") or []
    slot_sums = {}
    if isinstance(slots, list):
        for slot in slots:
            if not isinstance(slot, dict):
                continue
            name = slot.get("item")
            if not isinstance(name, str) or not name:
                continue
            if slot.get("quantity") is None:
                unknown.add(name)
                values.setdefault(name, None)
                continue
            if name not in totals:
                try:
                    slot_sums[name] = slot_sums.get(name, 0) + int(slot["quantity"])
                except (TypeError, ValueError, OverflowError):
                    unknown.add(name)
                    values[name] = None
    for name, quantity in slot_sums.items():
        if name not in unknown and name not in totals:
            values[name] = quantity
    return values, unknown


def _comparison_rows(old_totals, new_totals, old_unknown=None, new_unknown=None):
    """Compare item presence and quantities without treating unknown as zero."""
    old_totals = old_totals if isinstance(old_totals, dict) else {}
    new_totals = new_totals if isinstance(new_totals, dict) else {}
    old_unknown = set(old_unknown or ())
    new_unknown = set(new_unknown or ())
    names = sorted(set(old_totals) | set(new_totals) | old_unknown | new_unknown,
                   key=lambda value: str(value).lower())
    rows = []
    counts = {"added": 0, "removed": 0, "increased": 0,
              "decreased": 0, "same": 0, "unknown": 0}
    net = 0
    for name in names:
        old_present = name in old_totals or name in old_unknown
        new_present = name in new_totals or name in new_unknown
        old_qty = 0 if not old_present else None
        new_qty = 0 if not new_present else None
        try:
            if old_present and name not in old_unknown:
                old_qty = int(old_totals[name])
        except (TypeError, ValueError, OverflowError):
            pass
        try:
            if new_present and name not in new_unknown:
                new_qty = int(new_totals[name])
        except (TypeError, ValueError, OverflowError):
            pass
        if not old_present and new_present:
            status = "added"
        elif old_present and not new_present:
            status = "removed"
        elif old_qty is None or new_qty is None:
            status = "unknown"
        elif new_qty > old_qty:
            status = "increased"
        elif new_qty < old_qty:
            status = "decreased"
        else:
            status = "same"
        diff = new_qty - old_qty if old_qty is not None and new_qty is not None else None
        counts[status] += 1
        if diff is not None:
            net += diff
        rows.append((str(name), old_qty, new_qty, diff, status))
    return rows, counts, net


class ComparisonDialog(QDialog):
    """\u041f\u043e\u043a\u0430\u0437\u044b\u0432\u0430\u0435\u0442 \u0440\u0430\u0437\u043d\u0438\u0446\u0443 \u043c\u0435\u0436\u0434\u0443 \u0434\u0432\u0443\u043c\u044f \u0441\u043a\u0430\u043d\u0430\u043c\u0438."""

    def __init__(self, ch1, ch2, parent=None):
        super().__init__(parent)
        self.setWindowTitle("\u0421\u0440\u0430\u0432\u043d\u0435\u043d\u0438\u0435 \u0441\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0439")
        self.resize(850, 620)
        self.setMinimumSize(720, 500)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(10)
        root.addWidget(make_label("\u0421\u0440\u0430\u0432\u043d\u0435\u043d\u0438\u0435 \u0441\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0439", title=True))
        ts1 = ch1.get("timestamp", "?")
        ts2 = ch2.get("timestamp", "?")
        root.addWidget(make_label(
            f"\u0420\u0430\u043d\u044c\u0448\u0435: {ts1}    ->    \u041f\u043e\u0437\u0436\u0435: {ts2}", muted=True))

        old_totals, old_unknown = _comparison_snapshot(ch1)
        new_totals, new_unknown = _comparison_snapshot(ch2)
        rows, counts, total_diff = _comparison_rows(
            old_totals, new_totals, old_unknown, new_unknown)
        stack_sizes = _load_history_stack_sizes()
        self.table = QTableWidget(len(rows), 7)
        self.table.setHorizontalHeaderLabels([
            "\u041f\u0440\u0435\u0434\u043c\u0435\u0442",
            "\u0420\u0430\u043d\u044c\u0448\u0435", "\u0421\u0442\u0430\u043a\u043e\u0432",
            "\u041f\u043e\u0437\u0436\u0435", "\u0421\u0442\u0430\u043a\u043e\u0432",
            "\u0418\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u0435", "\u0421\u0442\u0430\u0442\u0443\u0441"])
        self.table.setColumnWidth(0, 220)
        for i in range(1, 7):
            self.table.setColumnWidth(i, 100)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)

        labels = {
            "added": "\u0414\u043e\u0431\u0430\u0432\u0438\u043b\u0441\u044f",
            "removed": "\u0418\u0441\u0447\u0435\u0437",
            "increased": "\u041a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432\u043e \u0432\u044b\u0440\u043e\u0441\u043b\u043e",
            "decreased": "\u041a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432\u043e \u0441\u043d\u0438\u0437\u0438\u043b\u043e\u0441\u044c",
            "same": "\u0411\u0435\u0437 \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u0439",
            "unknown": "\u041d\u0435\u043b\u044c\u0437\u044f \u0441\u0440\u0430\u0432\u043d\u0438\u0442\u044c",
        }
        colors = {
            "added": PALETTE["GREEN"],
            "removed": PALETTE["RED"],
            "increased": PALETTE["GREEN"],
            "decreased": PALETTE["RED"],
            "same": PALETTE["MUTED"],
            "unknown": PALETTE["MUTED"],
        }
        for row, (name, old_qty, new_qty, diff, status) in enumerate(rows):
            delta = (f"+{diff}" if diff > 0 else str(diff)) if diff is not None else "\u2014"
            stack_size = max(1, int(stack_sizes.get(name, 64)))
            old_text = str(old_qty) if old_qty is not None else "\u041d\u0435 \u0440\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u043d\u043e"
            new_text = str(new_qty) if new_qty is not None else "\u041d\u0435 \u0440\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u043d\u043e"
            old_stacks = str(old_qty // stack_size) if old_qty is not None else "\u2014"
            new_stacks = str(new_qty // stack_size) if new_qty is not None else "\u2014"
            values = [name, old_text, old_stacks, new_text, new_stacks, delta, labels[status]]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setForeground(QColor(
                    colors[status] if col == 6 else PALETTE["TEXT"]))
                self.table.setItem(row, col, cell)
            tint = QColor(colors[status])
            tint.setAlpha(28)
            for col in range(7):
                self.table.item(row, col).setBackground(tint)
        root.addWidget(self.table, 1)

        summary = QHBoxLayout()
        summary.addWidget(make_label(
            f"\u0414\u043e\u0431\u0430\u0432\u0438\u043b\u043e\u0441\u044c: {counts['added']}    "
            f"\u0418\u0441\u0447\u0435\u0437\u043b\u043e: {counts['removed']}    "
            f"\u0411\u043e\u043b\u044c\u0448\u0435: {counts['increased']}    "
            f"\u041c\u0435\u043d\u044c\u0448\u0435: {counts['decreased']}    "
            f"\u0411\u0435\u0437 \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u0439: {counts['same']}    "
            f"\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u043e: {counts['unknown']}",
            muted=True))
        summary.addStretch(1)
        dc = PALETTE["GREEN"] if total_diff >= 0 else PALETTE["RED"]
        ds = "+" if total_diff > 0 else ""
        summary.addWidget(make_label(
            f"\u0420\u0430\u0437\u043d\u0438\u0446\u0430 \u0438\u0437\u0432\u0435\u0441\u0442\u043d\u044b\u0445 \u043a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432: {ds}{total_diff}", color=dc))
        root.addLayout(summary)

        close_btn = HoverButton("\u0417\u0430\u043a\u0440\u044b\u0442\u044c")
        close_btn.clicked.connect(self.accept)
        root.addWidget(close_btn, 0, Qt.AlignHCenter)

# ── Экспорт ─────────────────────────────────────────────────────────
class ExportDialog(QDialog):
    """Экспорт данных в CSV/JSON."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Экспорт данных")
        self.resize(620, 360)
        self.load_error = None
        try:
            self.chests = [
                {key: value for key, value in chest.items() if key != "_source"}
                for chest in load_all_chests(strict=True)
            ]
        except Exception as exc:
            logger.exception("Unexpected error while loading history for export")
            self.chests = []
            self.load_error = str(exc)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(10)
        root.addWidget(make_label("Экспорт данных", title=True))
        root.addWidget(make_label(
            f"Доступно сканирований во всей истории: {len(self.chests)}", muted=True))
        if self.load_error:
            root.addWidget(make_label(
                f"Экспорт отменён: история повреждена или неполная. {self.load_error}",
                muted=True))
        elif not self.chests:
            root.addWidget(make_label("Пока нет сканирований для экспорта.", muted=True))

        def do_export(export_type, fmt):
            if self.load_error:
                QMessageBox.critical(self, "Экспорт", self.load_error)
                return
            if not self.chests:
                QMessageBox.information(self, "Экспорт", "Нет данных.")
                return
            ext = ".csv" if fmt == "csv" else ".json"
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path, _ = QFileDialog.getSaveFileName(
                self, "Сохранить файл", f"chest_export_{ts}{ext}",
                f"{fmt.upper()} (*{ext})")
            if not path:
                return
            try:
                if fmt == "json":
                    export_chests_to_json(self.chests, path)
                elif export_type == "summary":
                    items_db = load_json(paths.ITEMS_FILE, {})
                    db_items = items_db.get("items", []) if isinstance(items_db, dict) else []
                    if not isinstance(db_items, list):
                        db_items = []
                    stack_sizes = {
                        item.get("name"): item.get("stack_size", 64)
                        for item in db_items
                        if isinstance(item, dict) and isinstance(item.get("name"), str)
                    }
                    export_summary_to_csv(self.chests, path, stack_sizes=stack_sizes)
                else:
                    export_chests_to_csv(self.chests, path)
                QMessageBox.information(self, "Экспорт", f"Сохранено:\n{path}")
            except Exception as e:
                QMessageBox.critical(self, "Экспорт", str(e))

        for title, etype, fmts in [
            ("Полная выгрузка (все слоты)", "full", ["csv", "json"]),
            ("Сводная таблица (итоги)", "summary", ["csv"])]:
            lbl = make_label(title, big=True)
            root.addWidget(lbl)
            bf = QHBoxLayout()
            bf.setSpacing(8)
            for fmt in fmts:
                btn = HoverButton(fmt.upper(), accent=(fmt == "csv"))
                btn.clicked.connect(lambda _=False, t=etype, f=fmt: do_export(t, f))
                btn.setEnabled(bool(self.chests) and not self.load_error)
                if self.load_error:
                    btn.setToolTip("Экспорт недоступен: проверьте файлы истории")
                elif not self.chests:
                    btn.setToolTip("\u041d\u0435\u0442 \u0441\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0439 \u0434\u043b\u044f \u044d\u043a\u0441\u043f\u043e\u0440\u0442\u0430")
                bf.addWidget(btn)
            bf.addStretch(1)
            root.addLayout(bf)
            root.addSpacing(6)

        restore_btn = HoverButton("Восстановить историю из JSON", accent=True)
        restore_btn.setEnabled(not self.load_error)
        restore_btn.setToolTip(
            "Добавляет только новые записи; существующие сканирования не перезаписываются")
        restore_btn.clicked.connect(self._restore_json)
        root.addWidget(restore_btn, 0, Qt.AlignHCenter)

        root.addStretch(1)
        close_btn = HoverButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        root.addWidget(close_btn, 0, Qt.AlignHCenter)


    def _restore_json(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите резервную копию истории", "", "JSON (*.json)")
        if not path:
            return
        try:
            with results_history_guard(paths.RESULTS_FILE):
                current_chests = load_all_chests(strict=True)
                plan = prepare_history_restore(path, paths.RESULTS_FILE, current_chests)
        except Exception as exc:
            QMessageBox.critical(self, "Восстановление", str(exc))
            logger.exception("Unexpected error while preparing history restore")
            return
        added = plan["added"]
        skipped = plan["skipped_existing"]
        duplicates = plan["skipped_duplicates"]
        preview = (
            f"Будет добавлено новых сканирований: {added}\n"
            f"Уже имеющихся ID (будут пропущены): {skipped}\n"
            f"Повторных ID внутри файла (будут пропущены): {duplicates}\n\n"
            "Существующие записи не перезаписываются. Продолжить?"
        )
        if added == 0:
            QMessageBox.information(self, "Восстановление", preview)
            return
        if not _question(self, "Подтверждение восстановления", preview, default_no=True):
            return
        try:
            # Keep scan/undo/restore and the matching SQLite rebuild in one critical section.
            with results_history_guard(paths.RESULTS_FILE):
                latest_chests = load_all_chests(strict=True)
                backup_copy = apply_history_restore(
                    plan, existing_chests=latest_chests, lock_held=True)
                self.chests = load_all_chests()
                try:
                    storage.sync_from_json(paths.RESULTS_DB, load_all_chests(strict=True))
                    db_note = "SQLite обновлена."
                except Exception as sync_exc:
                    db_note = f"JSON восстановлен, но обновить SQLite не удалось: {sync_exc}"
        except Exception as exc:
            logger.exception("Unexpected error while applying history restore")
            QMessageBox.critical(self, "??????????????", str(exc))
            return
        backup_note = f"\nПредыдущая история сохранена: {backup_copy}" if backup_copy else ""
        QMessageBox.information(
            self, "Восстановление завершено",
            f"Добавлено записей: {added}.\nПропущено существующих: {skipped}.\n"
            f"Пропущено повторов в файле: {duplicates}.\n{db_note}{backup_note}"
        )



# ── Профили ─────────────────────────────────────────────────────────
class ProfileManagerDialog(QDialog):
    """Управление профилями: переключение, создание, переименование, удаление."""

    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self.setWindowTitle("Профили")
        self.resize(700, 500)
        self.profiles = load_profiles()
        self.selected_name = self.profiles.get("active", "Default")

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 18)
        root.setSpacing(6)
        root.addWidget(make_label("Профили", title=True))
        root.addWidget(make_label(
            "У каждого профиля своя база, настройки и результаты.", muted=True))

        self.active_label = make_label("", muted=True, small=True)
        root.addWidget(self.active_label)
        body = QHBoxLayout()
        body.setSpacing(12)
        root.addLayout(body, 1)
        self.listbox = QListWidget()
        self.listbox.itemDoubleClicked.connect(lambda _i: self._switch())
        body.addWidget(self.listbox, 1)
        self.listbox.currentItemChanged.connect(self._update_actions)

        btns = QVBoxLayout()
        btns.setSpacing(6)
        self.switch_btn = HoverButton("Переключить", accent=True)
        self.switch_btn.clicked.connect(self._switch)
        btns.addWidget(self.switch_btn)
        self.create_btn = HoverButton("Новый")
        self.create_btn.clicked.connect(self._create)
        btns.addWidget(self.create_btn)
        self.export_btn = HoverButton("Экспорт")
        self.export_btn.clicked.connect(self._export)
        btns.addWidget(self.export_btn)
        self.import_btn = HoverButton("Импорт")
        self.import_btn.clicked.connect(self._import)
        btns.addWidget(self.import_btn)
        self.backup_btn = HoverButton("Резервная копия")
        self.backup_btn.clicked.connect(self._backup)
        btns.addWidget(self.backup_btn)
        self.restore_btn = HoverButton("Восстановить")
        self.restore_btn.clicked.connect(self._restore)
        btns.addWidget(self.restore_btn)
        self.rename_btn = HoverButton("Переименовать")
        self.rename_btn.clicked.connect(self._rename)
        btns.addWidget(self.rename_btn)
        self.delete_btn = HoverButton("Удалить")
        self.delete_btn.clicked.connect(self._delete)
        btns.addWidget(self.delete_btn)
        btns.addStretch(1)
        close_btn = HoverButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        body.addLayout(btns)

        self._refresh_profiles()

    def _refresh_profiles(self):
        self.listbox.clear()
        active = self.profiles.get("active", "Default")
        self.active_label.setText(f"\u0422\u0435\u043a\u0443\u0449\u0438\u0439 \u043f\u0440\u043e\u0444\u0438\u043b\u044c: {active}")
        selected_row = -1
        for entry in self.profiles.get("profiles", []):
            name = entry.get("name", "Profile")
            suffix = "  \u2022 \u0410\u043a\u0442\u0438\u0432\u0435\u043d" if name == active else ""
            item = QListWidgetItem(name + suffix)
            item.setData(Qt.UserRole, name)
            self.listbox.addItem(item)
            if name == self.selected_name:
                selected_row = self.listbox.count() - 1
        if selected_row < 0:
            for row in range(self.listbox.count()):
                if self.listbox.item(row).data(Qt.UserRole) == active:
                    selected_row = row
                    break
        self.listbox.setCurrentRow(selected_row)
        self._update_actions()

    def _chosen(self):
        item = self.listbox.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _refresh_after_profile_mutation(self, selected_name):
        """Refresh the list without misreporting a committed operation as failed."""
        try:
            profiles = load_profiles()
            self.profiles = profiles
            available = {entry.get("name") for entry in profiles.get("profiles", [])}
            self.selected_name = (
                selected_name if selected_name in available
                else profiles.get("active", "Default"))
            self._refresh_profiles()
        except Exception as exc:
            logger.exception("Profile operation committed, but UI refresh failed")
            QMessageBox.warning(
                self, "???????",
                "???????? ? ???????? ?????????, ?? ?????? ?? ??????? ????????. "
                f"???????? ? ????? ???????? ???? ????????.\n\n{exc}")
            return False
        return True

    def _update_actions(self, *_):
        name = self._chosen()
        active = self.profiles.get("active", "Default")
        selected = bool(name)
        self.switch_btn.setEnabled(selected and name != active)
        self.rename_btn.setEnabled(selected and name != "Default")
        self.delete_btn.setEnabled(selected and name not in {"Default", active})
        self.export_btn.setEnabled(selected)
        self.backup_btn.setEnabled(selected)
        self.restore_btn.setEnabled(
            selected and name not in {"Default", active})

    def _switch(self):
        name = self._chosen()
        if not name:
            return
        if name == self.profiles.get("active"):
            self.accept()
            return
        if not _question(self, "Профиль", f"Переключить на «{name}»?"):
            return
        main = self.main
        if main.reader.auto_scan_active:
            if not main.reader.stop_auto_scan():
                main._sync_auto_scan_btn()
                main._log("\u0410\u0432\u0442\u043e\u0441\u043a\u0430\u043d \u0435\u0449\u0451 \u0437\u0430\u0432\u0435\u0440\u0448\u0430\u0435\u0442\u0441\u044f. \u041f\u043e\u0432\u0442\u043e\u0440\u0438\u0442\u0435 \u043f\u0435\u0440\u0435\u043a\u043b\u044e\u0447\u0435\u043d\u0438\u0435 \u0447\u0443\u0442\u044c \u043f\u043e\u0437\u0436\u0435.", PALETTE["YELLOW"])
                return
            main._sync_auto_scan_btn()
        try:
            main.reader.switch_profile(name)
        except Exception as exc:
            QMessageBox.critical(
                self, "Profile switch",
                f"Could not load profile '{name}'. "
                f"The previous profile remains active.\\n\\n{exc}")
            return
        main.reader.unknown_item_prompt = main._prompt_unknown_item
        main._refresh_profile_label()
        main._refresh_stats()
        main._log(f"✓ Профиль: {name}", PALETTE["GREEN"])
        self.selected_name = name
        self.accept()

    def _create(self):
        name, ok = QInputDialog.getText(
            self, "\u041d\u043e\u0432\u044b\u0439 \u043f\u0440\u043e\u0444\u0438\u043b\u044c",
            "\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if any(c in name for c in '/\\\\:*?"<>|'):
            QMessageBox.critical(
                self, "\u041f\u0440\u043e\u0444\u0438\u043b\u044c",
                "\u041d\u0435\u0434\u043e\u043f\u0443\u0441\u0442\u0438\u043c\u044b\u0435 \u0441\u0438\u043c\u0432\u043e\u043b\u044b.")
            return
        existing = [x.get("name", "") for x in self.profiles.get("profiles", [])]
        if any(value.casefold() == name.casefold() for value in existing):
            QMessageBox.critical(
                self, "\u041f\u0440\u043e\u0444\u0438\u043b\u044c",
                "\u041f\u0440\u043e\u0444\u0438\u043b\u044c \u0441 \u0442\u0430\u043a\u0438\u043c \u0438\u043c\u0435\u043d\u0435\u043c \u0443\u0436\u0435 \u0435\u0441\u0442\u044c.")
            return

        try:
            create_profile_data(name)
        except Exception as exc:
            QMessageBox.critical(self, "Профиль", str(exc))
            return

        if not self._refresh_after_profile_mutation(name):
            return
        self.main._log(
            f"\u2713 \u0421\u043e\u0437\u0434\u0430\u043d \u043f\u0440\u043e\u0444\u0438\u043b\u044c \u00ab{name}\u00bb.",
            PALETTE["GREEN"])

    def _export(self):
        name = self._chosen()
        if not name:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт профиля", f"{name}.chestprofile",
            "Профиль ChestScanner (*.chestprofile *.zip)")
        if not path:
            return
        if not path.lower().endswith((".chestprofile", ".zip")):
            path += ".chestprofile"
        try:
            export_profile_archive(name, path)
        except Exception as exc:
            QMessageBox.critical(self, "Экспорт профиля", str(exc))
            return
        QMessageBox.information(
            self, "Экспорт профиля",
            f"Профиль «{name}» экспортирован.\\n\\n{path}")

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Импорт профиля", "",
            "Профиль ChestScanner (*.chestprofile *.zip)")
        if not path:
            return
        try:
            entry = import_profile_archive(path)
        except Exception as exc:
            QMessageBox.critical(
                self, "Импорт профиля",
                f"Импорт отменён. Существующие профили не изменены.\\n\\n{exc}")
            return
        self.profiles = load_profiles()
        self.selected_name = entry["name"]
        self._refresh_profiles()
        self.main._log(
            f"Профиль «{entry['name']}» импортирован.", PALETTE["GREEN"])
        QMessageBox.information(
            self, "Импорт профиля",
            f"Профиль «{entry['name']}» импортирован. Он не активирован.")

    def _backup(self):
        name = self._chosen()
        if not name:
            return
        try:
            path = create_profile_backup(name)
        except Exception as exc:
            QMessageBox.critical(self, "Резервная копия", str(exc))
            return
        QMessageBox.information(
            self, "Резервная копия",
            f"Резервная копия профиля «{name}» создана.\\n\\n{path}\\n\\n"
            "Копия содержит историю и приватные настройки, включая Discord webhook. "
            "Хранится только на этом компьютере.")

    def _restore(self):
        name = self._chosen()
        if not name or name in {"Default", load_profiles().get("active")}:
            return
        backups = list_profile_backups(name)
        start_dir = os.path.dirname(backups[0]) if backups else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Восстановление профиля", start_dir,
            "Резервная копия ChestScanner (*.chestbackup)")
        if not path:
            return
        if not _question(
                self, "Восстановление профиля",
                f"Заменить все данные профиля «{name}» выбранной копией? "
                "Перед заменой будет создана резервная копия текущего состояния.",
                default_no=True):
            return
        try:
            restore_profile_backup(name, path)
        except Exception as exc:
            QMessageBox.critical(
                self, "Восстановление профиля",
                f"Восстановление отменено.\\n\\n{exc}")
            return
        QMessageBox.information(
            self, "Восстановление профиля",
            f"Профиль «{name}» восстановлен. Перезапустите приложение, "
            "чтобы перечитать его данные.")

    def _rename(self):
        name = self._chosen()
        if not name or name == "Default":
            QMessageBox.information(
                self, "\u041f\u0440\u043e\u0444\u0438\u043b\u044c",
                "Default \u043d\u0435\u043b\u044c\u0437\u044f \u043f\u0435\u0440\u0435\u0438\u043c\u0435\u043d\u043e\u0432\u0430\u0442\u044c.")
            return
        nn, ok = QInputDialog.getText(
            self, "\u041f\u0435\u0440\u0435\u0438\u043c\u0435\u043d\u043e\u0432\u0430\u043d\u0438\u0435",
            "\u041d\u043e\u0432\u043e\u0435 \u0438\u043c\u044f:", text=name)
        if not ok or not nn.strip():
            return
        nn = nn.strip()
        if any(c in nn for c in '/\\\\:*?"<>|'):
            QMessageBox.critical(
                self, "\u041f\u0440\u043e\u0444\u0438\u043b\u044c",
                "\u041d\u0435\u0434\u043e\u043f\u0443\u0441\u0442\u0438\u043c\u044b\u0435 \u0441\u0438\u043c\u0432\u043e\u043b\u044b.")
            return

        pd = load_profiles()
        if any(entry.get("name", "").casefold() == nn.casefold()
               and entry.get("name") != name
               for entry in pd.get("profiles", [])):
            QMessageBox.critical(
                self, "\u041f\u0440\u043e\u0444\u0438\u043b\u044c",
                "\u041f\u0440\u043e\u0444\u0438\u043b\u044c \u0441 \u0442\u0430\u043a\u0438\u043c \u0438\u043c\u0435\u043d\u0435\u043c \u0443\u0436\u0435 \u0435\u0441\u0442\u044c.")
            return
        entry = next((x for x in pd.get("profiles", [])
                      if x.get("name") == name), None)
        if not entry:
            return

        old_directory = entry.get("directory", ".")
        was_active = pd.get("active") == name
        main = self.main if was_active else None
        if was_active and main.reader.auto_scan_active:
            if not main.reader.stop_auto_scan():
                main._sync_auto_scan_btn()
                main._log("Автоскан ещё завершается. Повторите переименование чуть позже.",
                          PALETTE["YELLOW"])
                return
            main._sync_auto_scan_btn()

        try:
            rename_profile_data(name, nn)
        except Exception as exc:
            QMessageBox.critical(self, "Профиль", str(exc))
            return

        if was_active:
            try:
                main.reader.switch_profile(nn)
            except Exception as exc:
                try:
                    rename_profile_data(nn, name, directory=old_directory)
                except Exception:
                    logger.exception("Не удалось откатить переименование активного профиля")
                    QMessageBox.critical(
                        self, "Профиль",
                        "Не удалось загрузить профиль и автоматически вернуть прежнее имя. "
                        "Не удаляйте каталоги профилей; потребуется восстановление.")
                    return
                QMessageBox.critical(
                    self, "Профиль",
                    f"Не удалось загрузить профиль. Переименование отменено.\\n\\n{exc}")
                return
            main.reader.unknown_item_prompt = main._prompt_unknown_item
            main._refresh_profile_label()
            main._refresh_stats()
            main._log(f"Профиль переименован: {nn}", PALETTE["GREEN"])
        self._refresh_after_profile_mutation(nn)

    def _delete(self):
        name = self._chosen()
        if not name or name == "Default":
            QMessageBox.information(self, "Профиль", "Default нельзя удалить.")
            return
        if name == load_profiles().get("active"):
            QMessageBox.information(self, "Профиль", "Сначала переключитесь.")
            return
        if not _question(self, "Удаление", f"Удалить «{name}» и все данные?"):
            return
        try:
            leftover = delete_profile_data(name)
        except Exception as exc:
            QMessageBox.critical(
                self, "Профиль",
                f"Удаление отменено. Данные профиля сохранены.\\n\\n{exc}")
            return
        if leftover:
            QMessageBox.warning(
                self, "Профиль",
                "Запись профиля удалена, но его файлы не удалось окончательно очистить. "
                f"Данные сохранены в каталоге:\\n{leftover}")
        self._refresh_after_profile_mutation(None)


# ── Статистика ──────────────────────────────────────────────────────
class StatsDialog(QDialog):
    """Статистика из базы результатов (SQLite)."""

    def __init__(self, parent=None):
        from chest_scanner import storage
        super().__init__(parent)
        self.setWindowTitle("Статистика")
        self.resize(760, 560)
        self.setMinimumSize(680, 480)
        # Never replace a complete SQLite cache with a partial view of history.
        self.load_error = None
        try:
            with results_history_guard(paths.RESULTS_FILE):
                chests = load_all_chests(strict=True)
                storage.sync_from_json(paths.RESULTS_DB, chests)
            stats = storage.stats(paths.RESULTS_DB)
        except Exception as exc:
            self.load_error = str(exc)
            logger.exception("Statistics unavailable: authoritative history could not be reconciled")
            stats = {"chests": 0, "items_total": 0, "unique_items": 0,
                     "top_qty": [], "top_freq": []}

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(10)
        root.addWidget(make_label("Статистика", title=True))
        root.addWidget(make_label(
            "Агрегировано из базы результатов (SQLite).", muted=True))

        if self.load_error:
            warning = make_label(
                "?????????? ??????????: ??????? ?????????? ??? ????????. "
                "SQLite-??? ?? ???????????. " + self.load_error,
                muted=True)
            warning.setWordWrap(True)
            root.addWidget(warning)

        top = QHBoxLayout()
        top.setSpacing(10)
        cards = [("chests", "Сканирований"), ("items_total", "Всего предметов"),
                 ("unique_items", "Уникальных")]
        for key, title in cards:
            top.addWidget(StatCard(title, str(stats.get(key, 0))))
        root.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(10)
        root.addLayout(body, 1)

        panels = [("Топ по количеству", "top_qty"),
                  ("Топ по частоте появлений", "top_freq")]
        thread = []
        for header, key in panels:
            f = QFrame()
            f.setProperty("card", True)
            v = QVBoxLayout(f)
            v.setContentsMargins(14, 12, 14, 12)
            v.setSpacing(6)
            v.addWidget(make_label(header, section=True))
            entries = stats.get(key, [])
            empty_hint = make_label(
                "Пока нет данных. Выполните несколько сканирований.",
                muted=True, small=True)
            empty_hint.setWordWrap(True)
            empty_hint.setMaximumWidth(250)
            empty_hint.setAlignment(Qt.AlignCenter)
            empty_hint.setVisible(not entries)
            v.addWidget(empty_hint, 1)
            table = QTableWidget(len(entries), 2)
            table.setVisible(bool(entries))
            table.setHorizontalHeaderLabels(["Предмет", "Значение"])
            table.setColumnWidth(0, 150)
            table.setColumnWidth(1, 80)
            table.verticalHeader().setVisible(False)
            table.setEditTriggers(QTableWidget.NoEditTriggers)
            for row, (name, value) in enumerate(entries):
                table.setItem(row, 0, QTableWidgetItem(str(name)))
                table.setItem(row, 1, QTableWidgetItem(str(value)))
            v.addWidget(table, 1)
            body.addWidget(f, 1)
            thread.append(f)

        close_btn = HoverButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        root.addWidget(close_btn, 0, Qt.AlignHCenter)


# ── База предметов (просмотр и редактирование) ─────────────────────
class ItemDatabaseDialog(QDialog):
    """Просмотр и редактирование базы предметов."""

    def __init__(self, reader, parent=None, focus_search=False):
        super().__init__(parent)
        self.reader = reader
        self.main = parent
        self.setWindowTitle("База предметов")
        self.resize(1100, 680)
        self.setMinimumSize(820, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(4)
        root.addWidget(make_label("База предметов", title=True))
        description = make_label(
            "\u041f\u0440\u043e\u0441\u043c\u043e\u0442\u0440 \u0438 \u0440\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u0431\u0430\u0437\u044b. \u041f\u0435\u0440\u0435\u043d\u043e\u0441 \u043c\u0435\u0436\u0434\u0443 \u043a\u043e\u043c\u043f\u044c\u044e\u0442\u0435\u0440\u0430\u043c\u0438 \u2014 "
            "\u0447\u0435\u0440\u0435\u0437 \u044d\u043a\u0441\u043f\u043e\u0440\u0442 \u0438 \u0438\u043c\u043f\u043e\u0440\u0442 \u0431\u0430\u0437\u044b.", muted=True, small=True)
        description.setWordWrap(True)
        root.addWidget(description)

        def save_db(db):
            """Сохраняет базу и обновляет кэш/индексы сканера."""
            save_json(paths.ITEMS_FILE, db, backup=True)
            reader.template_cache.invalidate()
            reader._rebuild_indexes()
            if self.main is not None and hasattr(self.main, "_refresh_stats"):
                self.main._refresh_stats()

        def on_log(msg, ok=None):
            if self.main is None or not hasattr(self.main, "_log"):
                return
            color = None
            if ok is True:
                color = PALETTE["GREEN"]
            elif ok is False:
                color = PALETTE["RED"]
            self.main._log(msg, color)

        view = ItemDatabaseView(
            self,
            read_only=False,
            get_db=lambda: reader.items_db or {"items": []},
            save_db=save_db,
            on_log=on_log,
        )
        self.view = view
        self._search_shortcut = QShortcut(QKeySequence.Find, self)
        self._search_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._search_shortcut.activated.connect(self._focus_search)
        tabs = QTabWidget(self)
        tabs.addTab(view, "\u0411\u0430\u0437\u0430 \u043f\u0440\u0435\u0434\u043c\u0435\u0442\u043e\u0432")
        from chest_scanner.gui.item_db_view import UnknownItemsView
        review = UnknownItemsView(tabs, reader, save_db, on_log)
        tabs.addTab(review, "\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u044b\u0435 \u043f\u0440\u0435\u0434\u043c\u0435\u0442\u044b")
        root.addWidget(tabs, 1)
        view.refresh_list()
        if focus_search:
            QTimer.singleShot(0, self._focus_search)

    def _focus_search(self):
        """Move keyboard focus to the existing item search field."""
        self.view.search_edit.setFocus()
        self.view.search_edit.selectAll()
