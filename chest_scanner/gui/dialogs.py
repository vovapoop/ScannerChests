"""GUI dialog: все диалоговые окна приложения на PySide6."""
import json
import os
import shutil
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from chest_scanner import paths
from chest_scanner.commons import load_json, save_json
from chest_scanner.config import (
    activate_profile,
    load_profiles,
    profile_entry,
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
from chest_scanner.items import sanitize_filename


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
        data = load_profiles()
        if name not in {e.get("name") for e in data.get("profiles", [])}:
            profile_entry(name)
        activate_profile(name)
        reader.reload_profile()
        wh = self.webhook_edit.text().strip()
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
        add_btn = HoverButton("Добавить в базу", accent=True)
        add_btn.clicked.connect(self.accept)
        btns.addWidget(add_btn)
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
        idx = self.cat_combo.currentIndex()
        if idx >= 0:
            self.category_key = self.cat_combo.itemData(idx)
        super().accept()

    def item_name(self):
        return self.name_edit.text().strip()


# ── История ─────────────────────────────────────────────────────────
def load_all_chests():
    """Все сканирования: текущий файл результатов + архив history/."""
    chests = []
    data = load_json(paths.RESULTS_FILE, {"chests": []})
    if isinstance(data, dict):
        chests.extend(data.get("chests", []))
    if os.path.isdir(paths.HISTORY_DIR):
        for fn in sorted(os.listdir(paths.HISTORY_DIR)):
            if not fn.endswith(".json"):
                continue
            fp = os.path.join(paths.HISTORY_DIR, fn)
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    arch = json.load(f)
                if isinstance(arch, dict):
                    for ch in arch.get("chests", []):
                        ch["_source"] = fn
                        chests.append(ch)
                elif isinstance(arch, list):
                    for ch in arch:
                        ch["_source"] = fn
                        chests.append(ch)
            except (json.JSONDecodeError, OSError):
                continue
    chests.sort(key=lambda c: c.get("timestamp", ""), reverse=True)
    return chests


class HistoryDialog(QDialog):
    """История сканирований: фильтры, детали, сравнение."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("История сканирований")
        self.resize(1100, 700)
        self.setMinimumSize(950, 600)
        self._all_chests = load_all_chests()
        self._filtered = []

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)
        root.addWidget(make_label("История сканирований", title=True))

        # Фильтры
        ff = QFrame()
        ff.setProperty("card", True)
        fr = QHBoxLayout(ff)
        fr.setContentsMargins(14, 10, 14, 10)
        fr.setSpacing(8)
        fr.addWidget(make_label("Предмет:", muted=True, small=True))
        self.item_edit = QLineEdit()
        self.item_edit.setFixedWidth(160)
        fr.addWidget(self.item_edit)
        fr.addWidget(make_label("Мин. кол-во:", muted=True, small=True))
        self.qty_edit = QLineEdit()
        self.qty_edit.setFixedWidth(70)
        fr.addWidget(self.qty_edit)
        fr.addWidget(make_label("Дата от:", muted=True, small=True))
        self.from_edit = QLineEdit()
        self.from_edit.setFixedWidth(100)
        self.from_edit.setPlaceholderText("ДД.ММ.ГГГГ")
        fr.addWidget(self.from_edit)
        fr.addWidget(make_label("до:", muted=True, small=True))
        self.to_edit = QLineEdit()
        self.to_edit.setFixedWidth(100)
        self.to_edit.setPlaceholderText("ДД.ММ.ГГГГ")
        fr.addWidget(self.to_edit)
        fr.addStretch(1)
        apply_btn = HoverButton("Применить")
        apply_btn.clicked.connect(self._refresh_list)
        fr.addWidget(apply_btn)
        reset_btn = HoverButton("Сбросить")
        reset_btn.clicked.connect(self._clear_filters)
        fr.addWidget(reset_btn)
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
        left_wrap.setFixedWidth(400)
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
        self.table.setColumnWidth(0, 300)
        self.table.setColumnWidth(1, 90)
        self.table.setColumnWidth(2, 80)
        self.table.setColumnWidth(3, 160)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        rw.addWidget(self.table, 1)
        content.addWidget(right_wrap, 1)

        # Низ
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        compare_btn = HoverButton("Сравнить выбранные")
        compare_btn.clicked.connect(self._compare)
        bottom.addWidget(compare_btn)
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
        for fmt in ("%d.%m.%Y", "%d.%m"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return None

    def _apply_filters(self, chests):
        item_f = self.item_edit.text().strip().lower()
        qty_f = 0
        try:
            qty_f = int(self.qty_edit.text().strip() or "0")
        except ValueError:
            pass
        d_from = self._parse_date(self.from_edit.text())
        d_to = self._parse_date(self.to_edit.text())
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
                    cd = None
                if cd:
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
        self.listbox.clear()
        for ch in self._filtered:
            ts = ch.get("timestamp", "?")
            slots = ch.get("occupied_slots", 0)
            items = ch.get("unique_items", 0)
            src = ch.get("_source", "")
            label = f"{ts}  |  {slots} ячеек  |  {items} предм."
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
        self.detail_info.setText(info)
        self.table.setRowCount(0)
        totals = ch.get("totals", {})
        categories = ch.get("categories", {})
        item_cats = {}
        for cat, items in categories.items():
            for item_name in items:
                item_cats[item_name] = cat
        for name, qty in sorted(totals.items(), key=lambda x: x[0].lower()):
            stacks = qty // 64
            cat = item_cats.get(name, "uncategorized")
            cat_name = paths.CATEGORY_NAMES.get(cat, cat)
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 1, QTableWidgetItem(str(qty)))
            self.table.setItem(row, 2, QTableWidgetItem(str(stacks)))
            self.table.setItem(row, 3, QTableWidgetItem(cat_name))

    def _compare(self):
        rows = self.listbox.selectedIndexes()
        if len(rows) < 2:
            QMessageBox.information(
                self, "Сравнение",
                "Выберите два сканирования.\nУдерживайте Ctrl при клике.")
            return
        idx = [r.row() for r in rows][:2]
        ch1, ch2 = self._filtered[idx[0]], self._filtered[idx[1]]
        ComparisonDialog(ch1, ch2, self).exec()


# ── Сравнение ───────────────────────────────────────────────────────
class ComparisonDialog(QDialog):
    """Сравнение двух сканирований."""

    def __init__(self, ch1, ch2, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Сравнение сканирований")
        self.resize(800, 600)
        self.setMinimumSize(700, 500)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(10)
        root.addWidget(make_label("Сравнение сканирований", title=True))
        ts1, ts2 = ch1.get("timestamp", "?"), ch2.get("timestamp", "?")
        root.addWidget(make_label(f"Скан 1: {ts1}    ↔    Скан 2: {ts2}", muted=True))

        t1, t2 = ch1.get("totals", {}), ch2.get("totals", {})
        all_items = sorted(set(list(t1.keys()) + list(t2.keys())), key=lambda x: x.lower())

        self.table = QTableWidget(len(all_items), 5)
        self.table.setHorizontalHeaderLabels(["Предмет", "Скан 1", "Скан 2", "Разница", "Статус"])
        self.table.setColumnWidth(0, 280)
        for i in range(1, 5):
            self.table.setColumnWidth(i, 100)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)

        colors = {"added": PALETTE["GREEN"], "removed": PALETTE["RED"],
                  "same": PALETTE["MUTED"]}
        added = removed = same = 0
        total_diff = 0
        for row, item in enumerate(all_items):
            q1, q2 = t1.get(item, 0), t2.get(item, 0)
            diff = q2 - q1
            total_diff += diff
            if diff > 0:
                status = "added"
                dt, st = f"+{diff}", "Добавилось"
                added += 1
            elif diff < 0:
                status = "removed"
                dt, st = str(diff), "Убавилось"
                removed += 1
            else:
                status = "same"
                dt, st = "0", "Без изменений"
                same += 1
            vals = [item, str(q1), str(q2), dt, st]
            for col, val in enumerate(vals):
                cell = QTableWidgetItem(val)
                if col == 4 and status == "removed":
                    cell.setForeground(QColor(PALETTE["RED"]))
                else:
                    cell.setForeground(QColor(PALETTE["TEXT"]))
                self.table.setItem(row, col, cell)
            color = colors[status]
            for col in range(5):
                self.table.item(row, col).setBackground(QColor(color + "22"))
        root.addWidget(self.table, 1)

        summary = QHBoxLayout()
        summary.addWidget(make_label(
            f"Добавилось: {added}    Убавилось: {removed}    Без изменений: {same}",
            muted=True))
        summary.addStretch(1)
        dc = PALETTE["GREEN"] if total_diff >= 0 else PALETTE["RED"]
        ds = "+" if total_diff >= 0 else ""
        summary.addWidget(make_label(
            f"Общая разница: {ds}{total_diff}", color=dc))
        root.addLayout(summary)

        close_btn = HoverButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        root.addWidget(close_btn, 0, Qt.AlignHCenter)


# ── Экспорт ─────────────────────────────────────────────────────────
class ExportDialog(QDialog):
    """Экспорт данных в CSV/JSON."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Экспорт данных")
        self.resize(620, 300)
        data = load_json(paths.RESULTS_FILE, {"chests": []})
        self.chests = data.get("chests", []) if isinstance(data, dict) else []

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(10)
        root.addWidget(make_label("Экспорт данных", title=True))
        root.addWidget(make_label(
            f"Доступно сканирований: {len(self.chests)}", muted=True))

        def do_export(export_type, fmt):
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
                    export_summary_to_csv(self.chests, path)
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
                bf.addWidget(btn)
            bf.addStretch(1)
            root.addLayout(bf)
            root.addSpacing(6)

        root.addStretch(1)
        close_btn = HoverButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        root.addWidget(close_btn, 0, Qt.AlignHCenter)


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

        body = QHBoxLayout()
        body.setSpacing(12)
        root.addLayout(body, 1)
        self.listbox = QListWidget()
        self.listbox.itemDoubleClicked.connect(lambda _i: self._switch())
        body.addWidget(self.listbox, 1)

        btns = QVBoxLayout()
        btns.setSpacing(6)
        actions = [("Переключить", self._switch, True),
                   ("Новый", self._create, False),
                   ("Переименовать", self._rename, False),
                   ("Удалить", self._delete, False)]
        for text, cmd, accent in actions:
            b = HoverButton(text, accent=accent)
            b.clicked.connect(cmd)
            btns.addWidget(b)
        btns.addStretch(1)
        close_btn = HoverButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        body.addLayout(btns)

        self._refresh_profiles()

    def _refresh_profiles(self):
        self.listbox.clear()
        for e in self.profiles.get("profiles", []):
            m = "  ●" if e.get("name") == self.profiles.get("active") else ""
            self.listbox.addItem(e.get("name", "Profile") + m)
        for i, e in enumerate(self.profiles.get("profiles", [])):
            if e.get("name") == self.selected_name:
                self.listbox.setCurrentRow(i)

    def _chosen(self):
        row = self.listbox.currentRow()
        if row < 0:
            return None
        return self.listbox.item(row).text().replace("  ●", "")

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
            main.reader.stop_auto_scan()
            main._sync_auto_scan_btn()
        activate_profile(name)
        main.reader.reload_profile()
        main.reader.unknown_item_prompt = main._prompt_unknown_item
        main._refresh_profile_label()
        main._refresh_stats()
        main._log(f"✓ Профиль: {name}", PALETTE["GREEN"])
        self.selected_name = name
        self.accept()

    def _create(self):
        name, ok = QInputDialog.getText(self, "Новый профиль", "Название:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if any(c in name for c in '/\\:*?"<>|'):
            QMessageBox.critical(self, "Профиль", "Недопустимые символы.")
            return
        if any(x.get("name") == name for x in self.profiles.get("profiles", [])):
            QMessageBox.critical(self, "Профиль", "Уже существует.")
            return
        cur = paths.PROFILE_DIR
        entry = profile_entry(name)
        nd = os.path.join(paths.BASE_DIR, entry["directory"]) if entry.get("directory") != "." else paths.BASE_DIR
        os.makedirs(nd, exist_ok=True)
        try:
            for fn in ("config.json", "items.json"):
                s = os.path.join(cur, fn)
                if os.path.isfile(s):
                    shutil.copy2(s, os.path.join(nd, fn))
            si = os.path.join(cur, "items")
            if os.path.isdir(si):
                shutil.copytree(si, os.path.join(nd, "items"), dirs_exist_ok=True)
        except OSError as e:
            QMessageBox.critical(self, "Профиль", str(e))
            return
        self.profiles = load_profiles()
        self._refresh_profiles()
        self.main._log(f"✓ Создан профиль «{name}».", PALETTE["GREEN"])

    def _rename(self):
        name = self._chosen()
        if not name or name == "Default":
            QMessageBox.information(self, "Профиль", "Default нельзя переименовать.")
            return
        nn, ok = QInputDialog.getText(self, "Переименование", "Новое имя:", text=name)
        if not ok or not nn.strip():
            return
        nn = nn.strip()
        if any(c in nn for c in '/\\:*?"<>|'):
            QMessageBox.critical(self, "Профиль", "Недопустимые символы.")
            return
        pd = load_profiles()
        entry = next((x for x in pd.get("profiles", []) if x.get("name") == name), None)
        if not entry:
            return
        ndn = os.path.join("profiles", sanitize_filename(nn))
        oa, na = os.path.join(paths.BASE_DIR, entry.get("directory", "")), os.path.join(paths.BASE_DIR, ndn)
        try:
            if oa != na and os.path.isdir(oa):
                os.rename(oa, na)
        except OSError as e:
            QMessageBox.critical(self, "Профиль", str(e))
            return
        entry["name"] = nn
        entry["directory"] = ndn.replace("\\", "/")
        pd["active"] = nn if pd.get("active") == name else pd.get("active")
        save_profiles(pd)
        self.profiles = pd
        self._refresh_profiles()

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
        entry = next((x for x in load_profiles().get("profiles", []) if x.get("name") == name), None)
        if entry:
            rel = entry.get("directory")
            if rel:
                target = os.path.join(paths.BASE_DIR, rel)
                if not os.path.abspath(target).startswith(os.path.abspath(paths.BASE_DIR) + os.sep):
                    QMessageBox.critical(self, "Профиль", "Отказ: неверный путь профиля.")
                    return
                try:
                    shutil.rmtree(target)
                except OSError as e:
                    QMessageBox.warning(self, "Профиль",
                                        f"Каталог не удалён: {e}\nУдалена только запись.")
        profiles = load_profiles()
        profiles["profiles"] = [x for x in profiles.get("profiles", []) if x.get("name") != name]
        save_profiles(profiles)
        self.profiles = profiles
        self._refresh_profiles()


# ── Статистика ──────────────────────────────────────────────────────
class StatsDialog(QDialog):
    """Статистика из базы результатов (SQLite)."""

    def __init__(self, parent=None):
        from chest_scanner import storage
        super().__init__(parent)
        self.setWindowTitle("Статистика")
        self.resize(760, 560)
        self.setMinimumSize(680, 480)
        stats = storage.stats(paths.RESULTS_DB)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(10)
        root.addWidget(make_label("Статистика", title=True))
        root.addWidget(make_label(
            "Агрегировано из базы результатов (SQLite).", muted=True))

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
            table = QTableWidget(len(stats.get(key, [])), 2)
            table.setHorizontalHeaderLabels(["Предмет", "Значение"])
            table.setColumnWidth(0, 220)
            table.setColumnWidth(1, 100)
            table.verticalHeader().setVisible(False)
            table.setEditTriggers(QTableWidget.NoEditTriggers)
            for row, (name, value) in enumerate(stats.get(key, [])):
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

    def __init__(self, reader, parent=None):
        super().__init__(parent)
        self.reader = reader
        self.main = parent
        self.setWindowTitle("База предметов")
        self.resize(1100, 680)
        self.setMinimumSize(950, 580)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(4)
        root.addWidget(make_label("База предметов", title=True))
        root.addWidget(make_label(
            "Просмотр и редактирование базы. Перенос между компьютерами — "
            "через экспорт и импорт базы.",
            muted=True, small=True))

        def save_db(db):
            """Сохраняет базу и обновляет кэш/индексы сканера."""
            save_json(paths.ITEMS_FILE, db)
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
        root.addWidget(view, 1)
        view.refresh_list()
