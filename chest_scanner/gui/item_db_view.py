"""Переиспользуемое окно «База предметов»: просмотр и CRUD (PySide6).

Один класс используется двумя приложениями:
- клиентский сканер — read_only=True (только поиск, фильтр, предпросмотр);
- приложение владельца — read_only=False (полное управление базой).

Класс не зависит от GUI-миксинов сканера: получает палитру и колбеки
(get_db/save_db/on_log), поэтому одинаково работает и в QDialog, и во
Frames приложения владельца.
"""
import os
import shutil
import uuid

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from chest_scanner import paths
from chest_scanner.commons import absolute_path
from chest_scanner.gui.theme import PALETTE
from chest_scanner.items import copy_image_as_png, next_image_path, normalize_items_db

DEFAULT_PALETTE = dict(PALETTE)


def _thumb_pixmap(path, size=32):
    """Загружает изображение и уменьшает до size×size (или None)."""
    try:
        full = absolute_path(path)
        if not full or not os.path.isfile(full):
            return None
        pm = QPixmap(full)
        if pm.isNull():
            return None
        return pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    except Exception:
        return None


class ItemDatabaseView(QWidget):
    """Панель управления базой предметов.

    Параметры:
        parent      — родительский виджет.
        read_only   — True: только просмотр; False: + CRUD-кнопки.
        get_db      — callable(), возвращает изменяемый dict {"items": [...]}.
        save_db     — callable(db), сохраняет базу после изменений
                      (только при read_only=False; None — не сохранять).
        on_log      — callable(msg, ok=None), необязательный лог.
        palette     — словарь цветов (ключи из DEFAULT_PALETTE).
    """

    def __init__(self, parent, read_only=True, get_db=None, save_db=None,
                 on_log=None, palette=None):
        pal = dict(DEFAULT_PALETTE)
        if palette:
            pal.update(palette)
        self.pal = pal
        self.read_only = bool(read_only)
        self._get_db = get_db or (lambda: {"items": []})
        self._save_db = save_db
        self._on_log = on_log
        self._selected_id = None

        super().__init__(parent)
        self.setObjectName("itemDbView")
        self._build_ui()

    # ── вспомогательные ───────────────────────────────────────────────
    def _button(self, text, command, accent=False):
        btn = QPushButton(text, self)
        if accent:
            btn.setObjectName("accent")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setMinimumHeight(32)
        btn.clicked.connect(command)
        return btn

    def _log(self, msg, ok=None):
        if self._on_log:
            try:
                self._on_log(msg, ok)
            except Exception:
                pass

    def _items(self):
        return (self._get_db() or {}).get("items", [])

    def _find_item(self, iid):
        return next((i for i in self._items() if str(i.get("id")) == str(iid)), None)

    def _after_change(self, msg=None):
        self.refresh_list()
        if msg:
            self._log(msg, True)
        if self._save_db is not None:
            try:
                self._save_db(self._get_db())
            except Exception as e:
                self._log(f"Ошибка сохранения базы: {e}", False)

    # ── построение интерфейса ─────────────────────────────────────────
    def _build_ui(self):
        pal = self.pal
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(10)
        lbl = QLabel("Поиск")
        lbl.setProperty("muted", True)
        top.addWidget(lbl)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Название предмета...")
        self.search_edit.textChanged.connect(self.refresh_list)
        top.addWidget(self.search_edit, 1)
        self.cat_combo = QComboBox()
        self.cat_combo.addItem("Все категории")
        for _key, title in paths.ITEM_CATEGORIES:
            self.cat_combo.addItem(title)
        self.cat_combo.currentIndexChanged.connect(self.refresh_list)
        top.addWidget(self.cat_combo)
        root.addLayout(top)

        content = QHBoxLayout()
        content.setSpacing(10)
        root.addLayout(content, 1)

        # Список предметов
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Название", "Категория", "Стак", "Фото", "Вкл."])
        self.table.setColumnWidth(0, 300)
        self.table.setColumnWidth(1, 140)
        self.table.setColumnWidth(2, 60)
        self.table.setColumnWidth(3, 60)
        self.table.setColumnWidth(4, 55)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        # Мультивыделение: Shift+клик — диапазон, Ctrl+клик — точечно.
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.itemSelectionChanged.connect(self._show_selected)
        self.table.doubleClicked.connect(lambda _i: self._show_selected())
        content.addWidget(self.table, 1)

        # Горячие клавиши таблицы (действуют при фокусе на таблице).
        sel_all = QShortcut(QKeySequence("Ctrl+A"), self.table)
        sel_all.setContext(Qt.WidgetShortcut)
        sel_all.activated.connect(self._select_all)
        if not self.read_only:
            del_sc = QShortcut(QKeySequence.Delete, self.table)
            del_sc.setContext(Qt.WidgetShortcut)
            del_sc.activated.connect(self._delete_selected)

        # Панель деталей
        self.detail_panel = QWidget()
        dp = QVBoxLayout(self.detail_panel)
        dp.setContentsMargins(14, 14, 14, 14)
        dp.setSpacing(8)
        self.preview = QLabel("Выберите предмет")
        self.preview.setProperty("muted", True)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedSize(120, 120)
        self.preview.setStyleSheet(
            f"background: {pal['PANEL_2']}; border: 1px solid {pal['BORDER']};"
            " border-radius: 10px;")
        dp.addWidget(self.preview)
        self.details = QLabel("")
        self.details.setWordWrap(True)
        self.details.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        dp.addWidget(self.details)
        lbl2 = QLabel("Фотографии")
        lbl2.setProperty("section", True)
        dp.addWidget(lbl2)
        self.photo_list = QListWidget()
        dp.addWidget(self.photo_list, 1)
        self.detail_panel.setFixedWidth(300)
        content.addWidget(self.detail_panel)

        if not self.read_only:
            bottom = QHBoxLayout()
            bottom.setSpacing(6)
            for text, cmd, accent in [
                ("Добавить", self._add_item, True),
                ("Изменить", self._edit_item, False),
                ("Вкл/выкл", self._toggle_item, False),
                ("Удалить", self._delete_item, False),
            ]:
                bottom.addWidget(self._button(text, cmd, accent))
            bottom.addSpacing(12)
            for text, cmd in [
                ("Доб. фото", self._add_photo),
                ("Удал. фото", self._remove_photo),
                ("Переим. фото", self._rename_photo),
                ("Главное фото", self._make_primary),
            ]:
                bottom.addWidget(self._button(text, cmd))
            bottom.addStretch(1)
            root.addLayout(bottom)

        self.refresh_list()

    # ── список и детали ───────────────────────────────────────────────
    def refresh_list(self):
        selected = self._selected_id
        self.table.setRowCount(0)
        q = self.search_edit.text().strip().lower()
        cat_idx = self.cat_combo.currentIndex()
        cat_filter = None
        if cat_idx > 0:
            cat_filter = paths.ITEM_CATEGORIES[cat_idx - 1][0]
        for item in sorted(self._items(), key=lambda x: x.get("name", "").lower()):
            if q and q not in item.get("name", "").lower():
                continue
            if cat_filter and item.get("category", "uncategorized") != cat_filter:
                continue
            images = item.get("images") or ([item.get("image")] if item.get("image") else [])
            row = self.table.rowCount()
            self.table.insertRow(row)
            name_item = QTableWidgetItem(item.get("name", ""))
            pm = _thumb_pixmap(images[0]) if images else None
            if pm:
                name_item.setIcon(QIcon(pm))
            name_item.setData(Qt.UserRole, str(item.get("id", "")))
            self.table.setItem(row, 0, name_item)
            cat_name = paths.CATEGORY_NAMES.get(item.get("category", "uncategorized"), "?")
            self.table.setItem(row, 1, QTableWidgetItem(cat_name))
            self.table.setItem(row, 2, QTableWidgetItem(str(item.get("stack_size", 64))))
            self.table.setItem(row, 3, QTableWidgetItem(str(len(images))))
            self.table.setItem(row, 4, QTableWidgetItem("Да" if item.get("enabled", True) else "Нет"))
        if selected:
            for r in range(self.table.rowCount()):
                if self._find_item(self.table.item(r, 0).data(Qt.UserRole)):
                    self.table.selectRow(r)
                    break
        if self.table.rowCount() == 0:
            self._show_selected()

    def _selected_item(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        if not item:
            return None
        return self._find_item(item.data(Qt.UserRole))

    def _selected_items(self):
        """Все выбранные в таблице предметы (для мультивыделения)."""
        result = []
        for r in self.table.selectionModel().selectedRows():
            cell = self.table.item(r.row(), 0)
            if cell is None:
                continue
            item = self._find_item(cell.data(Qt.UserRole))
            if item:
                result.append(item)
        return result

    def _select_all(self):
        self.table.selectAll()

    def _delete_selected(self):
        """Пакетное удаление всех выбранных предметов (Delete)."""
        items = self._selected_items()
        if not items:
            return
        names = ", ".join(i.get("name", "") for i in items[:3])
        more = "" if len(items) <= 3 else f" и ещё {len(items) - 3}"
        ret = QMessageBox.question(
            self, "Удаление",
            f"Удалить выбранные предметы ({len(items)} шт.)?\n"
            f"{names}{more}\nФото будут удалены.")
        if ret != QMessageBox.Yes:
            return
        ids = {str(i.get("id")) for i in items}
        for item in items:
            for p in item.get("images", []):
                try:
                    fp = absolute_path(p)
                    if fp and os.path.isfile(fp):
                        os.remove(fp)
                except OSError:
                    pass
        db = self._get_db()
        db["items"] = [x for x in db.get("items", [])
                       if str(x.get("id")) not in ids]
        self._selected_id = None
        self._after_change(f"✓ Удалено предметов: {len(items)}.")

    def _show_selected(self, *_):
        item = self._selected_item()
        if not item:
            self._selected_id = None
            self.preview.setText("Выберите предмет")
            self.preview.setPixmap(QPixmap())
            self.details.setText("")
            self.photo_list.clear()
            return
        self._selected_id = item.get("id")
        images = item.get("images") or ([item.get("image")] if item.get("image") else [])
        cat_name = paths.CATEGORY_NAMES.get(item.get("category", "uncategorized"),
                                            "Без категории")
        tags = ", ".join(item.get("tags", [])) or "—"
        self.details.setText(
            f"Название: {item.get('name', '')}\n"
            f"Категория: {cat_name}\n"
            f"Теги: {tags}\n"
            f"Стак: {item.get('stack_size', 64)}\n"
            f"Фото: {len(images)}\n"
            f"Включён: {'Да' if item.get('enabled', True) else 'Нет'}")
        self.photo_list.clear()
        for p in images:
            self.photo_list.addItem(p)
        if images:
            pm = _thumb_pixmap(images[0], 112)
            if pm:
                self.preview.setPixmap(pm)
                return
        self.preview.setText("Нет фото")
        self.preview.setPixmap(QPixmap())

    # ── CRUD (только read_only=False) ─────────────────────────────────
    def _choose_category(self, initial="uncategorized"):
        titles = [c[1] for c in paths.ITEM_CATEGORIES]
        current = paths.CATEGORY_NAMES.get(initial, titles[0])
        index = titles.index(current) if current in titles else 0
        title, ok = QInputDialog.getItem(
            self, "Категория", "Выберите категорию:", titles, index, False)
        if not ok:
            return None
        for key, t in paths.ITEM_CATEGORIES:
            if t == title:
                return key
        return None

    def _ask_text(self, caption, label, initial=""):
        text, ok = QInputDialog.getText(self, caption, label, text=initial)
        return text.strip() if ok else None

    def _ask_image_files(self, title):
        files, _ = QFileDialog.getOpenFileNames(
            self, title, "",
            "Изображения (*.png *.jpg *.jpeg);;Все файлы (*.*)")
        return files

    def _add_item(self):
        name = self._ask_text("Новый предмет", "Название:")
        if not name:
            return
        st, ok = QInputDialog.getText(self, "Новый предмет", "Размер стака:", text="64")
        try:
            stack = max(1, int(st or "64"))
        except ValueError:
            stack = 64
        category = self._choose_category()
        if category is None:
            category = "uncategorized"
        sel_paths = self._ask_image_files("Фотографии предмета")
        item = {"id": str(uuid.uuid4()), "name": name, "stack_size": stack,
                "images": [], "image": "", "aliases": [], "enabled": True,
                "category": category, "tags": []}
        for src in sel_paths:
            rel = next_image_path(name, ".png")
            copy_image_as_png(src, rel)
            item["images"].append(rel)
        if item["images"]:
            item["image"] = item["images"][0]
        self._get_db().setdefault("items", []).append(item)
        self._after_change(f"✓ Добавлен предмет «{name}».")

    def _edit_item(self):
        item = self._selected_item()
        if not item:
            QMessageBox.information(self, "База", "Выберите предмет.")
            return
        old_name = item.get("name", "item")
        new_name = self._ask_text("Изменить", "Название:", old_name)
        if new_name is None:
            return
        new_name = new_name or old_name
        st, ok = QInputDialog.getText(self, "Изменить", "Размер стака:",
                                      text=str(item.get("stack_size", 64)))
        try:
            stack = max(1, int(st or item.get("stack_size", 64)))
        except ValueError:
            stack = item.get("stack_size", 64)
        category = self._choose_category(item.get("category", "uncategorized"))
        if category is not None:
            item["category"] = category
        if new_name != old_name:
            new_images = []
            for old_rel in item.get("images") or []:
                ext = os.path.splitext(old_rel)[1] or ".png"
                new_rel = next_image_path(new_name, ext)
                try:
                    of, nf = absolute_path(old_rel), absolute_path(new_rel)
                    if of and os.path.isfile(of) and os.path.abspath(of) != os.path.abspath(nf):
                        shutil.move(of, nf)
                    new_images.append(new_rel)
                except OSError:
                    new_images.append(old_rel)
            item["images"] = new_images
            item["image"] = new_images[0] if new_images else ""
        item["name"] = new_name
        item["stack_size"] = stack
        self._after_change()
        self._show_selected()

    def _toggle_item(self):
        item = self._selected_item()
        if not item:
            return
        item["enabled"] = not item.get("enabled", True)
        self._after_change()
        self._show_selected()

    def _delete_item(self):
        item = self._selected_item()
        if not item:
            return
        ret = QMessageBox.question(
            self, "Удаление", f"Удалить «{item.get('name', '')}»?\nФото будут удалены.")
        if ret != QMessageBox.Yes:
            return
        for p in item.get("images", []):
            try:
                fp = absolute_path(p)
                if fp and os.path.isfile(fp):
                    os.remove(fp)
            except OSError:
                pass
        db = self._get_db()
        db["items"] = [x for x in db.get("items", [])
                       if x.get("id") != item.get("id")]
        self._selected_id = None
        self._after_change(f"✓ Удалён предмет «{item.get('name', '')}».")

    def _add_photo(self):
        item = self._selected_item()
        if not item:
            QMessageBox.information(self, "Фото", "Выберите предмет.")
            return
        sel_paths = self._ask_image_files("Добавить фото")
        if not sel_paths:
            return
        images = item.get("images") or ([item.get("image")] if item.get("image") else [])
        for src in sel_paths:
            rel = next_image_path(item.get("name", "item"), ".png")
            copy_image_as_png(src, rel)
            images.append(rel)
        item["images"] = images
        item["image"] = images[0] if images else ""
        self._after_change("✓ Фотографии добавлены.")
        self._show_selected()

    def _current_photo_index(self):
        row = self.photo_list.currentRow()
        return row if row >= 0 else None

    def _remove_photo(self):
        item = self._selected_item()
        idx = self._current_photo_index()
        if not item or idx is None:
            return
        images = item.get("images") or []
        if idx >= len(images):
            return
        path = images[idx]
        ret = QMessageBox.question(self, "Удалить фото",
                                   f"Удалить {os.path.basename(path)}?")
        if ret != QMessageBox.Yes:
            return
        try:
            fp = absolute_path(path)
            if fp and os.path.isfile(fp):
                os.remove(fp)
        except OSError:
            pass
        images.pop(idx)
        item["images"] = images
        item["image"] = images[0] if images else ""
        self._after_change()
        self._show_selected()

    def _rename_photo(self):
        item = self._selected_item()
        idx = self._current_photo_index()
        if not item or idx is None:
            return
        images = item.get("images") or []
        if idx >= len(images):
            return
        path = images[idx]
        cur = os.path.splitext(os.path.basename(path))[0]
        base = self._ask_text("Переименовать фото", "Новое имя:", cur)
        if base is None:
            return
        base = base or cur
        ext = os.path.splitext(path)[1] or ".png"
        nr = next_image_path(base, ext)
        try:
            shutil.move(absolute_path(path), absolute_path(nr))
            images[idx] = nr
            item["images"] = images
            item["image"] = images[0] if images else ""
            self._after_change()
            self._show_selected()
        except OSError as e:
            QMessageBox.critical(self, "Фото", str(e))

    def _make_primary(self):
        item = self._selected_item()
        idx = self._current_photo_index()
        if not item or idx is None or idx == 0:
            return
        images = item.get("images") or []
        if idx >= len(images):
            return
        photo = images.pop(idx)
        images.insert(0, photo)
        item["images"] = images
        item["image"] = images[0]
        self._after_change()
        self._show_selected()


def normalize_for_save(db):
    """Приводит базу к единому виду перед сохранением."""
    return normalize_items_db(db or {"items": []})