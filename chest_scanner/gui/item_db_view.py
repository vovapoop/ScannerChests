"""Переиспользуемое окно «База предметов»: просмотр и CRUD (PySide6).

Один класс используется двумя приложениями:
- клиентский сканер — read_only=True (только поиск, фильтр, предпросмотр);
- приложение владельца — read_only=False (полное управление базой).

Класс не зависит от GUI-миксинов сканера: получает палитру и колбеки
(get_db/save_db/on_log), поэтому одинаково работает и в QDialog, и во
Frames приложения владельца.
"""
import filecmp
import os
import shutil
import tempfile
import uuid
from collections import OrderedDict
from copy import deepcopy

from PySide6.QtCore import QItemSelectionModel, QSettings, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImageReader, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from chest_scanner import paths
from chest_scanner.commons import absolute_path, profile_data_guard, save_json
from chest_scanner.gui.theme import PALETTE
from chest_scanner.items import copy_image_as_png, next_image_path, normalize_items_db
from chest_scanner.scanner.items import load_unknown_queue

DEFAULT_PALETTE = dict(PALETTE)

def _move_photo_safely(source, destination):
    """Move a photo without overwriting another file or leaving partial copies."""
    if not source or not destination:
        raise OSError("?? ??????? ?????????? ???? ??????????.")
    source = os.path.abspath(source)
    destination = os.path.abspath(destination)
    if os.path.normcase(source) == os.path.normcase(destination):
        return
    if not os.path.isfile(source):
        raise FileNotFoundError(source)
    if os.path.exists(destination):
        raise FileExistsError(destination)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    try:
        shutil.move(source, destination)
    except OSError:
        if os.path.isfile(source) and os.path.isfile(destination):
            try:
                os.remove(destination)
            except OSError:
                pass
        elif not os.path.exists(source) and os.path.isfile(destination):
            try:
                shutil.move(destination, source)
            except OSError:
                pass
        raise



def _ask_stack_size(parent, current=64):
    """Choose a stack size from presets or enter a custom positive value."""
    dialog = QDialog(parent)
    dialog.setWindowTitle("\u0420\u0430\u0437\u043c\u0435\u0440 \u0441\u0442\u0430\u043a\u0430")
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    preset = QComboBox()
    preset.addItem("64 \u0448\u0442\u0443\u043a\u0438", "64")
    preset.addItem("16 \u0448\u0442\u0443\u043a", "16")
    preset.addItem("\u0421\u0432\u043e\u0451 \u0447\u0438\u0441\u043b\u043e", "custom")
    custom = QSpinBox()
    custom.setRange(1, 999999)
    custom.setValue(max(1, int(current or 64)))
    value = int(current or 64)
    preset.setCurrentIndex(0 if value == 64 else 1 if value == 16 else 2)
    custom.setEnabled(preset.currentData() == "custom")
    preset.currentIndexChanged.connect(
        lambda _index: custom.setEnabled(preset.currentData() == "custom"))
    form.addRow("\u041f\u0440\u0435\u0441\u0435\u0442:", preset)
    form.addRow("\u041a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432\u043e:", custom)
    layout.addLayout(form)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if dialog.exec() != QDialog.Accepted:
        return None
    selected = preset.currentData()
    return int(selected) if selected != "custom" else custom.value()


class _NumericTableWidgetItem(QTableWidgetItem):
    """Sort numeric table cells by their numeric value, not displayed text."""

    def __lt__(self, other):
        try:
            return int(self.data(Qt.UserRole) or 0) < int(other.data(Qt.UserRole) or 0)
        except (TypeError, ValueError):
            return super().__lt__(other)


class _TextTableWidgetItem(QTableWidgetItem):
    """Sort text cells alphabetically without distinguishing letter case."""

    def __lt__(self, other):
        return self.text().casefold() < other.text().casefold()


class _PhotoDropListWidget(QListWidget):
    """Photo list that accepts image files dropped from the file manager."""

    IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

    def __init__(self, parent=None, files_dropped=None):
        super().__init__(parent)
        self.files_dropped = files_dropped

    @classmethod
    def _image_paths(cls, event):
        mime_data = event.mimeData()
        if not mime_data.hasUrls():
            return []
        return [
            url.toLocalFile() for url in mime_data.urls()
            if url.isLocalFile()
            and os.path.splitext(url.toLocalFile())[1].casefold() in cls.IMAGE_SUFFIXES
            and os.path.isfile(url.toLocalFile())
        ]

    def dragEnterEvent(self, event):
        if event.source() is self:
            super().dragEnterEvent(event)
        elif self._image_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.source() is self:
            super().dragMoveEvent(event)
        elif self._image_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if event.source() is self:
            super().dropEvent(event)
            return
        files = self._image_paths(event)
        if files and self.files_dropped:
            self.files_dropped(files)
            event.acceptProposedAction()
        else:
            event.ignore()


def _read_scaled_pixmap(full_path, bounds):
    """Decode an image directly to the largest size needed by the caller."""
    try:
        reader = QImageReader(full_path)
        reader.setAutoTransform(True)
        source_size = reader.size()
        if source_size.isValid():
            reader.setScaledSize(source_size.scaled(
                bounds, Qt.KeepAspectRatio))
        image = reader.read()
        if image.isNull():
            return None
        pixmap = QPixmap.fromImage(image)
        return None if pixmap.isNull() else pixmap
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def _photo_problem(path, check_readable=True):
    """Describe a missing or unreadable image without changing the database."""
    full = absolute_path(path)
    if not full or not os.path.isfile(full):
        return "\u0424\u0430\u0439\u043b \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d"
    if not check_readable:
        return None
    if _read_scaled_pixmap(full, QSize(32, 32)) is None:
        return "\u0424\u0430\u0439\u043b \u043f\u043e\u0432\u0440\u0435\u0436\u0434\u0451\u043d \u0438\u043b\u0438 \u0444\u043e\u0440\u043c\u0430\u0442 \u043d\u0435 \u043f\u043e\u0434\u0434\u0435\u0440\u0436\u0438\u0432\u0430\u0435\u0442\u0441\u044f"
    return None


class _ThumbnailCache:
    """Bounded LRU cache that decodes source images directly at thumbnail size."""

    DEFAULT_MAX_BYTES = 16 * 1024 * 1024

    def __init__(self, max_bytes=DEFAULT_MAX_BYTES):
        self.max_bytes = max(0, int(max_bytes))
        self._cache = OrderedDict()
        self._memory_bytes = 0

    @staticmethod
    def _pixmap_bytes(pixmap):
        return max(0, pixmap.width() * pixmap.height() * 4)

    @staticmethod
    def _key(full_path, size):
        stat = os.stat(full_path)
        return (os.path.normcase(os.path.realpath(full_path)), int(size),
                stat.st_mtime_ns, stat.st_size)

    def _remove_path_entries(self, full_path):
        canonical = os.path.normcase(os.path.realpath(full_path))
        for key in [key for key in self._cache if key[0] == canonical]:
            _, cost = self._cache.pop(key)
            self._memory_bytes -= cost

    def invalidate(self, path=None):
        """Drop cached thumbnails for one image or release the whole cache."""
        if path is None:
            self.clear()
            return
        full = absolute_path(path)
        if full:
            self._remove_path_entries(full)

    def get(self, path, size=32):
        try:
            size = int(size)
            if size <= 0:
                return None
            full = absolute_path(path)
            if not full:
                return None
            if not os.path.isfile(full):
                # A deleted image must not keep pixmaps resident indefinitely.
                self._remove_path_entries(full)
                return None
            key = self._key(full, size)
            # A replaced image invalidates every cached size variant.
            for stale_key in [
                    candidate for candidate in self._cache
                    if candidate[0] == key[0] and candidate[2:] != key[2:]]:
                _, stale_cost = self._cache.pop(stale_key)
                self._memory_bytes -= stale_cost
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached[0]

            reader = QImageReader(full)
            reader.setAutoTransform(True)
            source_size = reader.size()
            if source_size.isValid():
                reader.setScaledSize(source_size.scaled(
                    QSize(size, size), Qt.KeepAspectRatio))
            image = reader.read()
            if image.isNull():
                return None
            pixmap = QPixmap.fromImage(image)
            if pixmap.isNull():
                return None

            cost = self._pixmap_bytes(pixmap)
            if self.max_bytes and cost <= self.max_bytes:
                self._cache[key] = (pixmap, cost)
                self._memory_bytes += cost
                while self._cache and self._memory_bytes > self.max_bytes:
                    _, (_, removed_cost) = self._cache.popitem(last=False)
                    self._memory_bytes -= removed_cost
            return pixmap
        except (OSError, TypeError, ValueError, RuntimeError):
            return None

    def clear(self):
        self._cache.clear()
        self._memory_bytes = 0

    @property
    def memory_bytes(self):
        return self._memory_bytes


def _thumb_pixmap(path, size=32):
    """Return a cached thumbnail without retaining a full-resolution source."""
    return _thumbnail_cache.get(path, size)


_thumbnail_cache = _ThumbnailCache()


class _PhotoViewerDialog(QDialog):
    """Large image viewer with previous/next navigation and aspect-ratio fit."""

    def __init__(self, paths, start_index=0, parent=None):
        super().__init__(parent)
        self.paths = list(paths)
        self.index = max(0, min(int(start_index), len(self.paths) - 1))
        self.setWindowTitle("???????? ??????????")
        self.resize(960, 720)

        layout = QVBoxLayout(self)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumSize(320, 240)
        self.image_label.setStyleSheet("background: #17191d; border-radius: 6px;")
        layout.addWidget(self.image_label, 1)

        controls = QHBoxLayout()
        self.previous_button = QPushButton("? ??????????")
        self.previous_button.clicked.connect(self.show_previous)
        controls.addWidget(self.previous_button)
        self.counter_label = QLabel()
        self.counter_label.setAlignment(Qt.AlignCenter)
        controls.addWidget(self.counter_label, 1)
        self.next_button = QPushButton("????????? ?")
        self.next_button.clicked.connect(self.show_next)
        controls.addWidget(self.next_button)
        layout.addLayout(controls)

        previous = QShortcut(QKeySequence(Qt.Key_Left), self)
        previous.activated.connect(self.show_previous)
        following = QShortcut(QKeySequence(Qt.Key_Right), self)
        following.activated.connect(self.show_next)
        close = QShortcut(QKeySequence(Qt.Key_Escape), self)
        close.activated.connect(self.reject)
        self._update_image()

    def _update_image(self):
        if not self.paths:
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText("\u041d\u0435\u0442 \u0444\u043e\u0442\u043e\u0433\u0440\u0430\u0444\u0438\u0439")
            self.counter_label.setText("0 / 0")
            self.previous_button.setEnabled(False)
            self.next_button.setEnabled(False)
            return
        path = self.paths[self.index]
        full_path = absolute_path(path)
        available = self.image_label.size()
        if available.width() < 2 or available.height() < 2:
            available = QSize(900, 620)
        pixmap = (
            _read_scaled_pixmap(full_path, available)
            if full_path and os.path.isfile(full_path) else None)
        if pixmap is None:
            problem = _photo_problem(path, check_readable=False) or "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0437\u0430\u0433\u0440\u0443\u0437\u0438\u0442\u044c \u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u0435"
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText(
                f"\u26a0 {problem}\n{os.path.basename(str(path))}")
        else:
            self.image_label.setText("")
            self.image_label.setPixmap(pixmap)
        self.counter_label.setText(f"{self.index + 1} / {len(self.paths)}")
        self.previous_button.setEnabled(self.index > 0)
        self.next_button.setEnabled(self.index < len(self.paths) - 1)

    def show_previous(self):
        if self.index > 0:
            self.index -= 1
            self._update_image()

    def show_next(self):
        if self.index < len(self.paths) - 1:
            self.index += 1
            self._update_image()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_image()


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
        self._filter_settings = QSettings("ChestScanner", "ChestScanner")
        self._last_saved_db = deepcopy(self._get_db() or {"items": []})
        self._undo_record = None
        self._undo_tempdir = None

        super().__init__(parent)
        self.setObjectName("itemDbView")
        self._build_ui()

    # ── вспомогательные ───────────────────────────────────────────────
    def _update_filter_counts(self, items):
        """Show live item counts in category and recognition-state filters."""
        category_counts = {}
        enabled_count = 0
        for item in items:
            category = item.get("category", "uncategorized") or "uncategorized"
            category_counts[category] = category_counts.get(category, 0) + 1
            if item.get("enabled", True):
                enabled_count += 1

        category = self.cat_combo.currentData()
        status = self.status_combo.currentData()
        category_was_blocked = self.cat_combo.blockSignals(True)
        status_was_blocked = self.status_combo.blockSignals(True)
        try:
            self.cat_combo.setItemText(0, f"\u0412\u0441\u0435 \u043a\u0430\u0442\u0435\u0433\u043e\u0440\u0438\u0438 ({len(items)})")
            for index in range(1, self.cat_combo.count()):
                key = self.cat_combo.itemData(index)
                title = paths.CATEGORY_NAMES.get(key, self.cat_combo.itemText(index))
                self.cat_combo.setItemText(index, f"{title} ({category_counts.get(key, 0)})")
            self.status_combo.setItemText(0, f"\u0412\u0441\u0435 \u0441\u043e\u0441\u0442\u043e\u044f\u043d\u0438\u044f ({len(items)})")
            self.status_combo.setItemText(1, f"\u0412\u043a\u043b. ({enabled_count})")
            self.status_combo.setItemText(2, f"\u0412\u044b\u043a\u043b. ({len(items) - enabled_count})")
            category_index = self.cat_combo.findData(category)
            status_index = self.status_combo.findData(status)
            if category_index >= 0:
                self.cat_combo.setCurrentIndex(category_index)
            if status_index >= 0:
                self.status_combo.setCurrentIndex(status_index)
        finally:
            self.cat_combo.blockSignals(category_was_blocked)
            self.status_combo.blockSignals(status_was_blocked)

    def _filters_active(self):
        """Return whether any non-default database filter is active."""
        return bool(
            self.search_edit.text().strip()
            or self.cat_combo.currentData()
            or self.status_combo.currentData() != "all"
        )

    def _filter_changed(self, *_args):
        """Persist the last search and filters for the next database visit."""
        self._filter_settings.setValue("item_database/search", self.search_edit.text())
        self._filter_settings.setValue(
            "item_database/category", self.cat_combo.currentData() or "")
        self._filter_settings.setValue("item_database/status", self.status_combo.currentData())
        self.reset_filters_button.setEnabled(self._filters_active())
        # Category/status totals depend on the database, not on the search
        # text. Avoid rescanning and rebuilding both filter combos per keypress.
        self.refresh_list(update_filter_counts=False)

    def _reset_filters(self):
        """Clear search, category and status filters without changing table sorting."""
        search_was_blocked = self.search_edit.blockSignals(True)
        category_was_blocked = self.cat_combo.blockSignals(True)
        status_was_blocked = self.status_combo.blockSignals(True)
        try:
            self.search_edit.clear()
            self.cat_combo.setCurrentIndex(0)
            self.status_combo.setCurrentIndex(0)
        finally:
            self.search_edit.blockSignals(search_was_blocked)
            self.cat_combo.blockSignals(category_was_blocked)
            self.status_combo.blockSignals(status_was_blocked)
        self._filter_changed()

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

    def _after_change(self, msg=None, *, delete_files=(), rollback_files=(), restore_files=()):
        """Save a change and keep the database and image files consistent."""
        db = self._get_db()
        previous_db = deepcopy(self._last_saved_db)
        try:
            if self._save_db is not None:
                self._save_db(db)
        except Exception as exc:
            db.clear()
            db.update(deepcopy(self._last_saved_db))
            for new_path, old_path in restore_files:
                try:
                    new_full = absolute_path(new_path)
                    old_full = absolute_path(old_path)
                    if new_full and old_full and os.path.isfile(new_full):
                        os.makedirs(os.path.dirname(old_full), exist_ok=True)
                        shutil.move(new_full, old_full)
                except OSError:
                    pass
            for path in rollback_files:
                try:
                    full_path = absolute_path(path)
                    if full_path and os.path.isfile(full_path):
                        os.remove(full_path)
                except OSError:
                    pass
                _thumbnail_cache.invalidate(path)
            for new_path, old_path in restore_files:
                _thumbnail_cache.invalidate(new_path)
                _thumbnail_cache.invalidate(old_path)
            self.refresh_list()
            self._log(f"\u041e\u0448\u0438\u0431\u043a\u0430 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0438\u044f \u0431\u0430\u0437\u044b. \u0418\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f \u043e\u0442\u043c\u0435\u043d\u0435\u043d\u044b: {exc}", False)
            QMessageBox.critical(
                self, "\u041e\u0448\u0438\u0431\u043a\u0430 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0438\u044f",
                "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c \u0431\u0430\u0437\u0443. \u0418\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f \u043e\u0442\u043c\u0435\u043d\u0435\u043d\u044b, "
                "\u0438\u0441\u0445\u043e\u0434\u043d\u044b\u0435 \u0444\u043e\u0442\u043e\u0433\u0440\u0430\u0444\u0438\u0438 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u044b.")
            return False

        undo_tempdir = tempfile.TemporaryDirectory(prefix="ChestScanner-undo-")
        undo_files = {}
        undo_supported = True

        def remember(path, source=None):
            nonlocal undo_supported
            if path in undo_files:
                return
            source_full = absolute_path(source if source is not None else path)
            if source_full and os.path.isfile(source_full):
                backup = os.path.join(undo_tempdir.name, f"{uuid.uuid4().hex}.bak")
                try:
                    shutil.copy2(source_full, backup)
                except OSError:
                    undo_supported = False
                    return
                undo_files[path] = backup
            else:
                undo_files[path] = None

        referenced_images = set()
        for saved_item in db.get("items", []):
            saved_paths = saved_item.get("images") or (
                [saved_item.get("image")] if saved_item.get("image") else [])
            referenced_images.update(
                os.path.normcase(os.path.normpath(str(path)))
                for path in saved_paths if path)

        for path in delete_files:
            if os.path.normcase(os.path.normpath(str(path))) not in referenced_images:
                remember(path)
        for path in rollback_files:
            undo_files[path] = None
        for new_path, old_path in restore_files:
            remember(old_path, source=new_path)
            undo_files[new_path] = None

        self._last_saved_db = deepcopy(db)
        if undo_supported:
            self._undo_tempdir = undo_tempdir
            self._undo_record = {"database": previous_db, "files": undo_files}
            if hasattr(self, "undo_button"):
                self.undo_button.setEnabled(True)
        else:
            undo_tempdir.cleanup()
            self._undo_tempdir = None
            self._undo_record = None
            if hasattr(self, "undo_button"):
                self.undo_button.setEnabled(False)
            self._log("\u0418\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u0435 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u043e, \u043d\u043e \u043e\u0442\u043c\u0435\u043d\u0430 \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430: \u043d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c \u043a\u043e\u043f\u0438\u044e \u0444\u043e\u0442\u043e.", False)

        for path in delete_files:
            if os.path.normcase(os.path.normpath(str(path))) in referenced_images:
                continue
            try:
                full_path = absolute_path(path)
                if full_path and os.path.isfile(full_path):
                    os.remove(full_path)
            except OSError as exc:
                self._log(f"\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0443\u0434\u0430\u043b\u0438\u0442\u044c \u0444\u0430\u0439\u043b \u0444\u043e\u0442\u043e\u0433\u0440\u0430\u0444\u0438\u0438 {path}: {exc}", False)
            _thumbnail_cache.invalidate(path)
        for new_path, old_path in restore_files:
            _thumbnail_cache.invalidate(new_path)
            _thumbnail_cache.invalidate(old_path)
        self.refresh_list()
        if msg:
            self._log(msg, True)
        return True

    @staticmethod
    def _apply_undo_files(file_state):
        """Apply relative image paths mapped to backups or None (absent)."""
        for path, backup in file_state.items():
            full_path = absolute_path(path)
            if not full_path:
                raise OSError(f"\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u043f\u0440\u0435\u0434\u0435\u043b\u0438\u0442\u044c \u043f\u0443\u0442\u044c \u043a \u0444\u043e\u0442\u043e: {path}")
            if backup is None:
                if os.path.isfile(full_path):
                    os.remove(full_path)
                continue
            if not os.path.isfile(backup):
                raise OSError(f"\u041d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430 \u0440\u0435\u0437\u0435\u0440\u0432\u043d\u0430\u044f \u043a\u043e\u043f\u0438\u044f \u0444\u043e\u0442\u043e: {path}")
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            shutil.copy2(backup, full_path)

    def _undo_last_change(self):
        """Undo the last saved database edit, including associated images."""
        if not self._undo_record:
            return
        db = self._get_db()
        current_db = deepcopy(db)
        before_files = self._undo_record["files"]
        rollback_dir = tempfile.TemporaryDirectory(prefix="ChestScanner-undo-rollback-")
        after_files = {}
        files_applied = False
        try:
            for path in before_files:
                full_path = absolute_path(path)
                if full_path and os.path.isfile(full_path):
                    backup = os.path.join(rollback_dir.name, f"{uuid.uuid4().hex}.bak")
                    shutil.copy2(full_path, backup)
                    after_files[path] = backup
                else:
                    after_files[path] = None
            # Mark the file phase before applying it: a restore may fail
            # after changing only a subset of files, which still needs rollback.
            files_applied = True
            self._apply_undo_files(before_files)
            for path in before_files:
                _thumbnail_cache.invalidate(path)
            db.clear()
            db.update(deepcopy(self._undo_record["database"]))
            if self._save_db is not None:
                self._save_db(db)
        except Exception as exc:
            db.clear()
            db.update(current_db)
            if files_applied:
                try:
                    self._apply_undo_files(after_files)
                except OSError:
                    self._log("\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043f\u043e\u043b\u043d\u043e\u0441\u0442\u044c\u044e \u0432\u043e\u0441\с\u0442\u0430\u043d\u043e\u0432\u0438\u0442\u044c \u0444\u0430\u0439\u043b\u044b \u043f\u043e\u0441\u043b\u0435 \u043e\u0448\u0438\u0431\u043a\u0438 \u043e\u0442\u043c\u0435\u043d\u044b.", False)
            for path in before_files:
                _thumbnail_cache.invalidate(path)
            rollback_dir.cleanup()
            QMessageBox.critical(
                self, "\u041e\u0448\u0438\u0431\u043a\u0430 \u043e\u0442\u043c\u0435\u043d\u044b",
                f"\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0442\u043c\u0435\u043d\u0438\u0442\u044c \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u0435. \u0422\u0435\u043a\u0443\u0449\u0430\u044f \u0431\u0430\u0437\u0430 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0430.\\n{exc}")
            return

        rollback_dir.cleanup()
        self._last_saved_db = deepcopy(db)
        self._undo_record = None
        self._undo_tempdir = None
        if hasattr(self, "undo_button"):
            self.undo_button.setEnabled(False)
        self.refresh_list()
        self._log("\u041f\u043e\u0441\u043b\u0435\u0434\u043d\u0435\u0435 \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u0435 \u0431\u0430\u0437\u044b \u043e\u0442\u043c\u0435\u043d\u0435\u043d\u043e.", True)
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
        self.search_edit.setText(str(self._filter_settings.value("item_database/search", "") or ""))
        self.search_edit.textChanged.connect(self._filter_changed)
        top.addWidget(self.search_edit, 1)
        self.cat_combo = QComboBox()
        self.cat_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cat_combo.setMinimumContentsLength(8)
        self.cat_combo.setMinimumWidth(100)
        self.cat_combo.setMaximumWidth(170)
        self.cat_combo.addItem("Все категории")
        for _key, title in paths.ITEM_CATEGORIES:
            self.cat_combo.addItem(title, _key)
        saved_category = str(self._filter_settings.value("item_database/category", "") or "")
        saved_category_index = self.cat_combo.findData(saved_category)
        if saved_category_index >= 0:
            self.cat_combo.setCurrentIndex(saved_category_index)
        self.cat_combo.currentIndexChanged.connect(self._filter_changed)
        self.cat_combo.setToolTip("Фильтр по категории предмета")
        top.addWidget(self.cat_combo)
        self.status_combo = QComboBox()
        self.status_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.status_combo.setMinimumContentsLength(8)
        self.status_combo.setMinimumWidth(120)
        self.status_combo.setMaximumWidth(145)
        self.status_combo.addItem("Все состояния", "all")
        self.status_combo.addItem("\u0412\u043a\u043b.", "enabled")
        self.status_combo.addItem("\u0412\u044b\u043a\u043b.", "disabled")
        self.status_combo.setToolTip("Фильтр по состоянию распознавания")
        saved_status = str(self._filter_settings.value("item_database/status", "all"))
        saved_status_index = self.status_combo.findData(saved_status)
        if saved_status_index >= 0:
            self.status_combo.setCurrentIndex(saved_status_index)
        self.status_combo.currentIndexChanged.connect(self._filter_changed)
        top.addWidget(self.status_combo)
        self.reset_filters_button = self._button("Сбросить", self._reset_filters)
        self.reset_filters_button.setToolTip(
            "Очистить поиск и вернуть фильтры к значениям по умолчанию")
        self.reset_filters_button.setEnabled(False)
        top.addWidget(self.reset_filters_button)
        self.result_label = QLabel("")
        self.result_label.setProperty("muted", True)
        self.result_label.setToolTip("\u041f\u043e\u043a\u0430\u0437\u0430\u043d\u043e / \u0432\u0441\u0435\u0433\u043e \u043f\u0440\u0435\u0434\u043c\u0435\u0442\u043e\u0432")
        self.result_label.setMinimumWidth(80)
        self.result_label.setMaximumWidth(105)
        top.addWidget(self.result_label)
        root.addLayout(top)

        content = QHBoxLayout()
        content.setSpacing(10)
        root.addLayout(content, 1)

        # Список предметов
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Название", "Категория", "Стак", "Фото", "Вкл."])
        header = self.table.horizontalHeader()
        try:
            sort_column = int(self._filter_settings.value(
                "item_database/sort_column", 0))
        except (TypeError, ValueError):
            sort_column = 0
        if not 0 <= sort_column < self.table.columnCount():
            sort_column = 0
        try:
            sort_order_value = int(self._filter_settings.value(
                "item_database/sort_order", Qt.AscendingOrder.value))
        except (TypeError, ValueError):
            sort_order_value = Qt.AscendingOrder.value
        sort_order = (
            Qt.DescendingOrder
            if sort_order_value == Qt.DescendingOrder.value
            else Qt.AscendingOrder
        )
        header.setSortIndicator(sort_column, sort_order)
        self.table.setSortingEnabled(True)
        header.sortIndicatorChanged.connect(self._save_sort_settings)
        for column, tip in enumerate((
                "??????????? ?? ????????",
                "??????????? ?? ?????????",
                "??????????? ?? ??????? ?????",
                "??????????? ?? ?????????? ??????????",
                "??????????? ?? ????????? ?????????????")):
            self.table.horizontalHeaderItem(column).setToolTip(tip)
        self.table.setColumnWidth(0, 260)
        self.table.setColumnWidth(1, 120)
        self.table.setColumnWidth(2, 55)
        self.table.setColumnWidth(3, 50)
        self.table.setColumnWidth(4, 45)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        # Мультивыделение: Shift+клик — диапазон, Ctrl+клик — точечно.
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.itemSelectionChanged.connect(self._show_selected)
        self.table.doubleClicked.connect(
            lambda _i: self._edit_item() if not self.read_only else self._show_selected())
        content.addWidget(self.table, 1)

        # Горячие клавиши таблицы (действуют при фокусе на таблице).
        sel_all = QShortcut(QKeySequence("Ctrl+A"), self.table)
        sel_all.setContext(Qt.WidgetShortcut)
        sel_all.activated.connect(self._select_all)

        clear_selection = QShortcut(QKeySequence(Qt.Key_Escape), self.table)
        clear_selection.setContext(Qt.WidgetShortcut)
        clear_selection.activated.connect(self._clear_selection)

        activate_item = QShortcut(QKeySequence(Qt.Key_Return), self.table)
        activate_item.setContext(Qt.WidgetShortcut)
        activate_item.activated.connect(self._activate_current_item)
        activate_item_enter = QShortcut(QKeySequence(Qt.Key_Enter), self.table)
        activate_item_enter.setContext(Qt.WidgetShortcut)
        activate_item_enter.activated.connect(self._activate_current_item)
        if not self.read_only:
            del_sc = QShortcut(QKeySequence.Delete, self.table)
            del_sc.setContext(Qt.WidgetShortcut)
            del_sc.activated.connect(self._delete_selected)

            add_sc = QShortcut(QKeySequence("Ctrl+N"), self)
            add_sc.setContext(Qt.WidgetWithChildrenShortcut)
            add_sc.activated.connect(self._add_item)

            edit_sc = QShortcut(QKeySequence("F2"), self.table)
            edit_sc.setContext(Qt.WidgetShortcut)
            edit_sc.activated.connect(self._edit_item)

            self._copy_shortcut = QShortcut(QKeySequence("Ctrl+D"), self.table)
            self._copy_shortcut.setContext(Qt.WidgetShortcut)
            self._copy_shortcut.activated.connect(self._duplicate_item)

        # Панель деталей
        self.detail_panel = QWidget()
        dp = QVBoxLayout(self.detail_panel)
        dp.setContentsMargins(14, 14, 14, 14)
        dp.setSpacing(8)
        self.preview = QLabel("Выберите предмет")
        self.preview.setProperty("muted", True)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedSize(144, 144)
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
        self.photo_list.setIconSize(QSize(40, 40))
        self.photo_list.setSpacing(3)
        self.photo_list.currentRowChanged.connect(self._show_photo_preview)
        self.photo_list.itemDoubleClicked.connect(self._open_photo_viewer)
        self.photo_list.setToolTip("?????? ???????? ??????????, ????? ??????? ??????? ????????")
        dp.addWidget(self.photo_list, 1)
        self.detail_panel.setMinimumWidth(220)
        self.detail_panel.setMaximumWidth(280)
        content.addWidget(self.detail_panel)

        if not self.read_only:
            actions = QHBoxLayout()
            actions.setSpacing(8)
            self.add_button = self._button("\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c", self._add_item, True)
            self.add_button.setToolTip("\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u043d\u043e\u0432\u044b\u0439 \u043f\u0440\u0435\u0434\u043c\u0435\u0442 (Ctrl+N)")
            self.edit_button = self._button("\u0418\u0437\u043c\u0435\u043d\u0438\u0442\u044c", self._edit_item)
            self.copy_button = self._button("\u041a\u043e\u043f\u0438\u0440\u043e\u0432\u0430\u0442\u044c", self._duplicate_item)
            self.copy_button.setToolTip("\u0421\u043e\u0437\u0434\u0430\u0442\u044c \u043a\u043e\u043f\u0438\u044e \u0432\u044b\u0431\u0440\u0430\u043d\u043d\u043e\u0433\u043e \u043f\u0440\u0435\u0434\u043c\u0435\u0442\u0430 (Ctrl+D)")
            self.toggle_button = self._button("\u041e\u0442\u043a\u043b\u044e\u0447\u0438\u0442\u044c", self._toggle_item)
            self.toggle_button.setToolTip("\u0412\u043a\u043b\u044e\u0447\u0438\u0442\u044c \u0438\u043b\u0438 \u043e\u0442\u043a\u043b\u044e\u0447\u0438\u0442\u044c \u0440\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u0432\u0430\u043d\u0438\u0435")
            self.delete_button = self._button("\u0423\u0434\u0430\u043b\u0438\u0442\u044c", self._delete_selected)
            self.add_button.setToolTip("\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u043f\u0440\u0435\u0434\u043c\u0435\u0442 (Ctrl+N)")
            self.edit_button.setToolTip("\u0418\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u0432\u044b\u0431\u0440\u0430\u043d\u043d\u044b\u0439 \u043f\u0440\u0435\u0434\u043c\u0435\u0442 (F2 \u0438\u043b\u0438 \u0434\u0432\u043e\u0439\u043d\u043e\u0439 \u0449\u0435\u043b\u0447\u043e\u043a)")
            for button in (self.add_button, self.edit_button, self.copy_button,
                           self.toggle_button, self.delete_button):
                actions.addWidget(button)
            actions.addStretch(1)
            root.addLayout(actions)

            photos = QHBoxLayout()
            photos.setSpacing(8)
            self.add_photo_button = self._button("\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u0444\u043e\u0442\u043e", self._add_photo)
            self.manage_photos_button = self._button("\u0423\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435 \u0444\u043e\u0442\u043e", self._manage_photos)
            self.undo_button = self._button("\u041e\u0442\u043c\u0435\u043d\u0438\u0442\u044c", self._undo_last_change)
            self.undo_button.setToolTip("\u041e\u0442\u043c\u0435\u043d\u0438\u0442\u044c \u043f\u043e\u0441\u043b\u0435\u0434\u043d\u0435\u0435 \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u0435 \u0431\u0430\u0437\u044b")
            self.undo_button.setEnabled(False)
            photos.addWidget(self.add_photo_button)
            photos.addWidget(self.manage_photos_button)
            photos.addWidget(self.undo_button)
            photos.addStretch(1)
            root.addLayout(photos)

        self.refresh_list()

    # ── список и детали ───────────────────────────────────────────────
    def _save_sort_settings(self, column, order):
        """Persist the active table sort column and direction."""
        self._filter_settings.setValue("item_database/sort_column", int(column))
        self._filter_settings.setValue("item_database/sort_order", order.value)

    def refresh_list(self, *, update_filter_counts=True):
        """Обновляет список, сохраняя выделение по стабильному ID."""
        selected_ids = {str(self.table.item(row.row(), 0).data(Qt.UserRole))
                        for row in self.table.selectionModel().selectedRows()
                        if self.table.item(row.row(), 0) is not None}
        current_index = self.table.selectionModel().currentIndex()
        current_item = (
            self.table.item(current_index.row(), 0)
            if current_index.isValid() else None)
        current_id = (
            str(current_item.data(Qt.UserRole)) if current_item is not None
            else self._selected_id)
        if not selected_ids and self._selected_id is not None:
            selected_ids.add(str(self._selected_id))
        updates_were_enabled = self.table.updatesEnabled()
        sorting_was_enabled = self.table.isSortingEnabled()
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)
        try:
            self.table.setRowCount(0)
            query = self.search_edit.text().strip().casefold()
            cat_filter = self.cat_combo.currentData()
            status_filter = self.status_combo.currentData()
            all_items = self._items()
            if update_filter_counts:
                self._update_filter_counts(all_items)
            # Filter once before touching the widget, then allocate the final row
            # count in one operation instead of repeatedly inserting rows.
            visible_items = []
            for item in all_items:
                name = str(item.get("name", ""))
                category = str(item.get("category", "uncategorized") or "uncategorized")
                category_name = paths.CATEGORY_NAMES.get(category, "\u0411\u0435\u0437 \u043a\u0430\u0442\u0435\u0433\u043e\u0440\u0438\u0438")
                searchable_text = (name.casefold(), category_name.casefold(),
                                   category.casefold())
                if query and not any(query in value for value in searchable_text):
                    continue
                if cat_filter and item.get("category", "uncategorized") != cat_filter:
                    continue
                enabled = item.get("enabled", True)
                if status_filter == "enabled" and not enabled:
                    continue
                if status_filter == "disabled" and enabled:
                    continue
                visible_items.append((item, name, category, category_name))

            self.table.setRowCount(len(visible_items))
            visible_ids = set()
            for row, (item, name, category, category_name) in enumerate(visible_items):
                images = item.get("images") or ([item.get("image")] if item.get("image") else [])
                name_item = _TextTableWidgetItem(name)
                pm = _thumb_pixmap(images[0]) if images else None
                if pm:
                    name_item.setIcon(QIcon(pm))
                iid = str(item.get("id", ""))
                name_item.setData(Qt.UserRole, iid)
                self.table.setItem(row, 0, name_item)
                self.table.setItem(row, 1, _TextTableWidgetItem(paths.CATEGORY_NAMES.get(item.get("category", "uncategorized"), "Без категории")))
                stack_item = _NumericTableWidgetItem(str(item.get("stack_size", 64)))
                stack_item.setData(Qt.UserRole, int(item.get("stack_size", 64) or 0))
                self.table.setItem(row, 2, stack_item)
                photo_item = _NumericTableWidgetItem(str(len(images)))
                photo_item.setData(Qt.UserRole, len(images))
                # Avoid decoding every database image during a table refresh; only
                # check file presence here. The selected item's preview performs
                # the full readability check.
                problems = [(os.path.basename(str(path)),
                             _photo_problem(path, check_readable=False))
                            for path in images]
                problems = [(name, problem) for name, problem in problems if problem]
                if problems:
                    photo_item.setToolTip("\u041f\u0440\u043e\u0431\u043b\u0435\u043c\u044b \u0441 \u0444\u043e\u0442\u043e\u0433\u0440\u0430\u0444\u0438\u044f\u043c\u0438:\n" + "\n".join(
                        f"{name}: {problem}" for name, problem in problems))
                self.table.setItem(row, 3, photo_item)
                self.table.setItem(row, 4, _TextTableWidgetItem("\u0414\u0430" if item.get("enabled", True) else "\u041d\u0435\u0442"))
                if iid in selected_ids:
                    visible_ids.add(iid)
                    self.table.selectionModel().select(
                        self.table.model().index(row, 0),
                        QItemSelectionModel.Select | QItemSelectionModel.Rows)
            self.table.setSortingEnabled(sorting_was_enabled)
            if current_id is not None:
                for row in range(self.table.rowCount()):
                    cell = self.table.item(row, 0)
                    if cell is not None and str(cell.data(Qt.UserRole)) == current_id:
                        self.table.setCurrentCell(
                            row, 0, QItemSelectionModel.NoUpdate)
                        break
            self.result_label.setText(
                f"{self.table.rowCount()} / {len(all_items)}")
            if not visible_ids:
                self._selected_id = None
            self._show_selected()
        finally:
            self.table.setSortingEnabled(sorting_was_enabled)
            self.table.setUpdatesEnabled(updates_were_enabled)

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

    def _clear_selection(self):
        """Clear row selection without changing the active sort or filters."""
        self.table.clearSelection()
        self.table.setCurrentItem(None)

    def _activate_current_item(self):
        """Activate the current row using the same action as a double-click."""
        if self.read_only:
            self._show_selected()
        else:
            self._edit_item()

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
        image_paths = [
            path for item in items
            for path in (item.get("images") or ([item.get("image")] if item.get("image") else []))
            if path
        ]
        db = self._get_db()
        db["items"] = [x for x in db.get("items", [])
                       if str(x.get("id")) not in ids]
        self._selected_id = None
        self._after_change(f"✓ Удалено предметов: {len(items)}.", delete_files=image_paths)

    def _show_selected(self, *_):
        selected = self._selected_items()
        if not selected:
            self._selected_id = None
            self.preview.setText("\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u043f\u0440\u0435\u0434\u043c\u0435\u0442")
            self.preview.setPixmap(QPixmap())
            self.details.clear()
            self.photo_list.clear()
            if not self.read_only:
                self.edit_button.setEnabled(False)
                self.toggle_button.setEnabled(False)
                self.toggle_button.setText("\u041e\u0442\u043a\u043b\u044e\u0447\u0438\u0442\u044c")
                self.toggle_button.setToolTip("\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u043f\u0440\u0435\u0434\u043c\u0435\u0442")
                self.add_photo_button.setEnabled(False)
                self.manage_photos_button.setEnabled(False)
            return
        if len(selected) > 1:
            self._selected_id = None
            self.preview.setText("Несколько предметов")
            self.preview.setPixmap(QPixmap())
            self.photo_list.clear()
            self.details.setText(
                f"Выбрано предметов: {len(selected)}\\n"
                "Можно удалить их одной операцией.")
            if not self.read_only:
                self.edit_button.setEnabled(False)
                self.toggle_button.setEnabled(False)
                self.delete_button.setEnabled(True)
                self.delete_button.setText(f"Удалить ({len(selected)})")
                self.add_photo_button.setEnabled(False)
                self.manage_photos_button.setEnabled(False)
            return
        item = selected[0]
        self._selected_id = item.get("id")
        images = item.get("images") or ([item.get("image")] if item.get("image") else [])
        if not self.read_only:
            self.edit_button.setEnabled(True)
            self.toggle_button.setEnabled(True)
            self.toggle_button.setText(
                "\u041e\u0442\u043a\u043b\u044e\u0447\u0438\u0442\u044c" if item.get("enabled", True) else "\u0412\u043a\u043b\u044e\u0447\u0438\u0442\u044c")
            self.toggle_button.setToolTip(
                "\u041e\u0442\u043a\u043b\u044e\u0447\u0438\u0442\u044c \u0440\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u0432\u0430\u043d\u0438\u0435" if item.get("enabled", True)
                else "\u0412\u043a\u043b\u044e\u0447\u0438\u0442\u044c \u0440\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u0432\u0430\u043d\u0438\u0435")
            self.delete_button.setEnabled(True)
            self.delete_button.setText("\u0423\u0434\u0430\u043b\u0438\u0442\u044c")
            self.add_photo_button.setEnabled(True)
            self.manage_photos_button.setEnabled(bool(images))
        cat_name = paths.CATEGORY_NAMES.get(item.get("category", "uncategorized"), "Без категории")
        self.details.setText(
            f"Название: {item.get('name', '')}\n"
            f"Категория: {cat_name}\n"
            f"Стак: {item.get('stack_size', 64)}\n"
            f"Фото: {len(images)}\n"
            f"Распознавание: {'Вкл.' if item.get('enabled', True) else 'Выкл.'}")
        self.photo_list.clear()
        for path in images:
            name = os.path.basename(str(path)) or str(path)
            problem = _photo_problem(path)
            photo = QListWidgetItem(
                f"? {problem} ? {name}" if problem else name)
            thumbnail = _thumb_pixmap(path, 40)
            if thumbnail:
                photo.setIcon(QIcon(thumbnail))
            photo.setData(Qt.UserRole, path)
            photo.setToolTip(
                f"{problem}: {path}" if problem else str(path))
            self.photo_list.addItem(photo)
        if images:
            self.photo_list.setCurrentRow(0)
        else:
            self._show_photo_preview(-1)

    def _open_photo_viewer(self, photo_item=None):
        """Open all selected item's photos in a navigable large preview."""
        if not self.photo_list.count():
            return
        row = self.photo_list.currentRow()
        if photo_item is not None:
            row = self.photo_list.row(photo_item)
        paths = [self.photo_list.item(i).data(Qt.UserRole)
                 for i in range(self.photo_list.count())]
        _PhotoViewerDialog(paths, max(0, row), self).exec()

    def _show_photo_preview(self, row):
        """Show the image currently selected in the photo list."""
        photo = self.photo_list.item(row) if row >= 0 else None
        path = photo.data(Qt.UserRole) if photo is not None else None
        if path:
            pm = _thumb_pixmap(path, 136)
            self.preview.setToolTip(str(path))
            if pm:
                self.preview.setPixmap(pm)
                self.preview.setText("")
                return
            problem = _photo_problem(path) or "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0437\u0430\u0433\u0440\u0443\u0437\u0438\u0442\u044c \u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u0435"
            self.preview.setPixmap(QPixmap())
            self.preview.setText(f"\u26a0 {problem}\n{os.path.basename(str(path))}")
            return
        self.preview.setToolTip("")
        self.preview.setPixmap(QPixmap())
        self.preview.setText("??? ????")

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
            "Изображения (*.png *.jpg *.jpeg *.webp *.bmp);;Все файлы (*.*)")
        return files

    def _add_item(self):
        name = self._ask_text("????? ???????", "????????:")
        if not name:
            return
        stack = _ask_stack_size(self)
        if stack is None:
            return
        category = self._choose_category()
        if category is None:
            category = "uncategorized"
        sel_paths = self._ask_image_files("?????????? ????????")
        item = {"id": str(uuid.uuid4()), "name": name, "stack_size": stack,
                "images": [], "image": "", "enabled": True,
                "category": category}
        try:
            for src in sel_paths:
                rel = next_image_path(name, ".png")
                # Include the current destination before copying: the converter
                # may create a partial file and then raise an exception.
                item["images"].append(rel)
                copy_image_as_png(src, rel)
        except Exception as exc:
            for rel in item["images"]:
                try:
                    full_path = absolute_path(rel)
                    if full_path and os.path.isfile(full_path):
                        os.remove(full_path)
                except OSError:
                    pass
            QMessageBox.critical(
                self, "?????? ?????????? ??????????", str(exc))
            return

        if item["images"]:
            item["image"] = item["images"][0]
        self._get_db().setdefault("items", []).append(item)
        self._after_change(
            f"? ???????? ??????? ?{name}?.", rollback_files=item["images"])

    def _duplicate_item(self):
        """Create an independent copy of the selected item and its image files."""
        item = self._selected_item()
        if not item:
            return
        old_name = str(item.get("name", "\u041f\u0440\u0435\u0434\u043c\u0435\u0442"))
        new_name = self._ask_text(
            "\u041a\u043e\u043f\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u043f\u0440\u0435\u0434\u043c\u0435\u0442", "\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u043a\u043e\u043f\u0438\u0438:", f"{old_name} (\u043a\u043e\u043f\u0438\u044f)")
        if not new_name:
            return
        new_name = new_name.strip()
        if not new_name:
            return
        if any(
                str(other.get("name", "")).casefold() == new_name.casefold()
                for other in self._items()):
            QMessageBox.warning(
                self, "\u0422\u0430\u043a\u043e\u0439 \u043f\u0440\u0435\u0434\u043c\u0435\u0442 \u0443\u0436\u0435 \u0435\u0441\u0442\u044c",
                f"\u041f\u0440\u0435\u0434\u043c\u0435\u0442 \u0441 \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u0435\u043c \u00ab{new_name}\u00bb \u0443\u0436\u0435 \u0441\u0443\u0449\u0435\u0441\u0442\u0432\u0443\u0435\u0442.")
            return

        source_images = item.get("images") or (
            [item.get("image")] if item.get("image") else [])
        copied_images = []
        try:
            for old_rel in source_images:
                if not old_rel:
                    continue
                source = absolute_path(old_rel)
                if not source or not os.path.isfile(source):
                    continue
                extension = os.path.splitext(str(old_rel))[1] or ".png"
                new_rel = next_image_path(new_name, extension)
                destination = absolute_path(new_rel)
                if not destination:
                    raise OSError("\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u043f\u0440\u0435\u0434\u0435\u043b\u0438\u0442\u044c \u043f\u0443\u0442\u044c \u0434\u043b\u044f \u043a\u043e\u043f\u0438\u0438 \u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u044f.")
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                shutil.copy2(source, destination)
                copied_images.append(new_rel)
        except OSError as exc:
            for rel_path in copied_images:
                try:
                    copied_path = absolute_path(rel_path)
                    if copied_path and os.path.isfile(copied_path):
                        os.remove(copied_path)
                except OSError:
                    pass
            QMessageBox.critical(self, "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0441\u043a\u043e\u043f\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u043f\u0440\u0435\u0434\u043c\u0435\u0442", str(exc))
            return

        duplicate = dict(item)
        duplicate.update({
            "id": str(uuid.uuid4()),
            "name": new_name,
            "images": copied_images,
            "image": copied_images[0] if copied_images else "",
        })
        self._items().append(duplicate)
        self._selected_id = duplicate["id"]
        self._after_change(f"\u2713 \u0421\u043e\u0437\u0434\u0430\u043d\u0430 \u043a\u043e\u043f\u0438\u044f \u043f\u0440\u0435\u0434\u043c\u0435\u0442\u0430 \u00ab{new_name}\u00bb.", rollback_files=copied_images)

    def _edit_item(self):
        item = self._selected_item()
        if not item:
            QMessageBox.information(self, "\u0411\u0430\u0437\u0430", "\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u043f\u0440\u0435\u0434\u043c\u0435\u0442.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u043f\u0440\u0435\u0434\u043c\u0435\u0442\u0430")
        dialog.setMinimumWidth(420)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()

        name_edit = QLineEdit(str(item.get("name", "")))
        name_edit.setObjectName("itemNameEdit")
        name_edit.selectAll()
        name_edit.setFocus()
        form.addRow("\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435:", name_edit)

        stack_combo = QComboBox()
        stack_combo.setObjectName("stackPresetCombo")
        stack_combo.addItem("64 \u0448\u0442\u0443\u043a\u0438", 64)
        stack_combo.addItem("16 \u0448\u0442\u0443\u043a", 16)
        stack_combo.addItem("\u0421\u0432\u043e\u0451 \u0447\u0438\u0441\u043b\u043e", "custom")
        current_stack = max(1, int(item.get("stack_size", 64) or 64))
        stack_combo.setCurrentIndex(
            0 if current_stack == 64 else 1 if current_stack == 16 else 2)
        stack_custom = QSpinBox()
        stack_custom.setRange(1, 999999)
        stack_custom.setValue(current_stack)
        stack_custom.setEnabled(stack_combo.currentData() == "custom")
        stack_combo.currentIndexChanged.connect(
            lambda _index: stack_custom.setEnabled(
                stack_combo.currentData() == "custom"))
        stack_row = QVBoxLayout()
        stack_row.addWidget(stack_combo)
        stack_row.addWidget(stack_custom)
        form.addRow("\u0420\u0430\u0437\u043c\u0435\u0440 \u0441\u0442\u0430\u043a\u0430:", stack_row)

        category_combo = QComboBox()
        category_combo.setObjectName("itemCategoryCombo")
        for key, title in paths.ITEM_CATEGORIES:
            category_combo.addItem(title, key)
        category_index = category_combo.findData(
            item.get("category", "uncategorized"))
        category_combo.setCurrentIndex(max(0, category_index))
        form.addRow("\u041a\u0430\u0442\u0435\u0433\u043e\u0440\u0438\u044f:", category_combo)


        enabled_check = QCheckBox("\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u044c \u043f\u0440\u0438 \u0440\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u0432\u0430\u043d\u0438\u0438")
        enabled_check.setChecked(item.get("enabled", True))
        form.addRow("\u0421\u0442\u0430\u0442\u0443\u0441:", enabled_check)
        layout.addLayout(form)

        hint = QLabel("\u0424\u043e\u0442\u043e\u0433\u0440\u0430\u0444\u0438\u0438 \u043c\u043e\u0436\u043d\u043e \u0434\u043e\u0431\u0430\u0432\u0438\u0442\u044c, \u0443\u0434\u0430\u043b\u0438\u0442\u044c \u0438\u043b\u0438 \u0432\u044b\u0431\u0440\u0430\u0442\u044c \u0433\u043b\u0430\u0432\u043d\u0443\u044e "
                      "\u043a\u043d\u043e\u043f\u043a\u0430\u043c\u0438 \u043f\u043e\u0434 \u0441\u043f\u0438\u0441\u043a\u043e\u043c \u043f\u0440\u0435\u0434\u043c\u0435\u0442\u043e\u0432.")
        hint.setWordWrap(True)
        hint.setProperty("muted", True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("\u0421\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c")
        buttons.button(QDialogButtonBox.Save).setDefault(True)
        buttons.button(QDialogButtonBox.Cancel).setText("\u041e\u0442\u043c\u0435\u043d\u0430")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return

        new_name = name_edit.text().strip()
        if not new_name:
            QMessageBox.warning(self, "\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435", "\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u043f\u0440\u0435\u0434\u043c\u0435\u0442\u0430 \u043d\u0435 \u043c\u043e\u0436\u0435\u0442 \u0431\u044b\u0442\u044c \u043f\u0443\u0441\u0442\u044b\u043c.")
            return

        selected_stack = stack_combo.currentData()
        stack = (stack_custom.value() if selected_stack == "custom"
                 else int(selected_stack))
        category = category_combo.currentData() or "uncategorized"


        old_name = item.get("name", "item")
        if new_name != old_name:
            images = item.get("images") or (
                [item.get("image")] if item.get("image") else [])
            moved_image_pairs = []
            renamed_images = []
            for old_rel in images:
                ext = os.path.splitext(old_rel)[1] or ".png"
                new_rel = next_image_path(new_name, ext)
                try:
                    old_file = absolute_path(old_rel)
                    new_file = absolute_path(new_rel)
                    moved = False
                    if (old_file and os.path.isfile(old_file)
                            and os.path.abspath(old_file) != os.path.abspath(new_file)):
                        try:
                            shutil.move(old_file, new_file)
                        except OSError:
                            # shutil.move may leave a partial destination when it
                            # falls back to copy/delete across volumes. Keep the
                            # original and remove only this newly allocated target.
                            if os.path.isfile(old_file) and os.path.isfile(new_file):
                                try:
                                    os.remove(new_file)
                                except OSError:
                                    pass
                            elif not os.path.isfile(old_file) and os.path.isfile(new_file):
                                try:
                                    shutil.move(new_file, old_file)
                                except OSError:
                                    pass
                            raise
                        moved_image_pairs.append((new_rel, old_rel))
                        moved = True
                    renamed_images.append(new_rel if moved else old_rel)
                except OSError:
                    renamed_images.append(old_rel)
            item["images"] = renamed_images
            item["image"] = renamed_images[0] if renamed_images else ""

        item["name"] = new_name
        item["stack_size"] = stack
        item["category"] = category
        item["enabled"] = enabled_check.isChecked()
        self._after_change(f"\u2713 \u041e\u0431\u043d\u043e\u0432\u043b\u0451\u043d \u043f\u0440\u0435\u0434\u043c\u0435\u0442 \u00ab{new_name}\u00bb.", restore_files=moved_image_pairs)
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
        image_paths = item.get("images") or (
            [item.get("image")] if item.get("image") else [])
        db = self._get_db()
        db["items"] = [x for x in db.get("items", [])
                       if x.get("id") != item.get("id")]
        self._selected_id = None
        self._after_change(f"✓ Удалён предмет «{item.get('name', '')}».", delete_files=image_paths)

    def _add_photo(self):
        item = self._selected_item()
        if not item:
            QMessageBox.information(self, "Фото", "Выберите предмет.")
            return
        sel_paths = self._ask_image_files("Добавить фото")
        if not sel_paths:
            return
        images = item.get("images") or ([item.get("image")] if item.get("image") else [])
        added_images = []
        try:
            for src in sel_paths:
                rel = next_image_path(item.get("name", "item"), ".png")
                copy_image_as_png(src, rel)
                images.append(rel)
                added_images.append(rel)
        except OSError as exc:
            for rel in added_images:
                full_path = absolute_path(rel)
                if full_path and os.path.isfile(full_path):
                    os.remove(full_path)
            QMessageBox.critical(self, "\u041e\u0448\u0438\u0431\u043a\u0430 \u0434\u043e\u0431\u0430\u0432\u043b\u0435\u043d\u0438\u044f \u0444\u043e\u0442\u043e", str(exc))
            return
        item["images"] = images
        item["image"] = images[0] if images else ""
        if self._after_change("\u2713 \u0424\u043e\u0442\u043e\u0433\u0440\u0430\u0444\u0438\u0438 \u0434\u043e\u0431\u0430\u0432\u043b\u0435\u043d\u044b.", rollback_files=added_images):
            self._show_selected()

    def _manage_photos(self):
        item = self._selected_item()
        if not item:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"?????????? ????: {item.get('name', '')}")
        dialog.resize(700, 440)
        layout = QVBoxLayout(dialog)
        count_label = QLabel()
        count_label.setProperty("muted", True)
        layout.addWidget(count_label)

        content = QHBoxLayout()
        photo_list = _PhotoDropListWidget(dialog)
        photo_list.setIconSize(QSize(56, 56))
        photo_list.setSpacing(4)
        photo_list.setMinimumWidth(220)
        photo_list.setDragEnabled(True)
        photo_list.setAcceptDrops(True)
        photo_list.setDropIndicatorShown(True)
        photo_list.setDragDropMode(QAbstractItemView.InternalMove)
        photo_list.setDefaultDropAction(Qt.MoveAction)
        photo_list.setToolTip("?????????? ?????????? ?????, ????? ???????? ???????. ?????? ?????????? ????????? ???????.")
        content.addWidget(photo_list, 1)
        preview = QLabel("???????? ??????????")
        preview.setAlignment(Qt.AlignCenter)
        preview.setMinimumSize(280, 260)
        preview.setStyleSheet(
            f"background: {self.pal['PANEL_2']}; border: 1px solid {self.pal['BORDER']};"
            " border-radius: 8px;")
        content.addWidget(preview, 2)
        layout.addLayout(content, 1)

        def update_preview(index):
            images = item.get("images") or []
            if not 0 <= index < len(images):
                preview.setPixmap(QPixmap())
                preview.setText("??? ????????? ??????????")
                return
            path = images[index]
            full_path = absolute_path(path)
            pixmap = QPixmap(full_path) if full_path and os.path.isfile(full_path) else QPixmap()
            if pixmap.isNull():
                preview.setPixmap(QPixmap())
                preview.setText(f"?? ??????? ???????\n{os.path.basename(path)}")
            else:
                preview.setText("")
                preview.setPixmap(pixmap.scaled(
                    preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            preview.setToolTip(path)

        def save_dragged_order(*_):
            """Persist the order produced by an internal drag-and-drop move."""
            images = [photo_list.item(index).data(Qt.UserRole)
                      for index in range(photo_list.count())]
            selected_path = (
                photo_list.currentItem().data(Qt.UserRole)
                if photo_list.currentItem() else None)
            previous = list(item.get("images") or [])
            if images == previous:
                return

            item["images"] = images
            item["image"] = images[0] if images else ""
            if self._after_change():
                self._show_selected()
                refresh(images.index(selected_path) if selected_path in images else 0)
                return

            # _after_change restores the database from its saved snapshot. The
            # original local item reference is then stale, so re-read it by ID.
            restored_item = self._find_item(item.get("id"))
            if restored_item is not None:
                item.update(restored_item)
            refresh(previous.index(selected_path) if selected_path in previous else 0)

        def refresh(select=0):
            photo_list.clear()
            images = item.get("images") or []
            for index, path in enumerate(images):
                problem = _photo_problem(path)
                label = (
                    "\u2605 \u0413\u043b\u0430\u0432\u043d\u0430\u044f  \u00b7  "
                    if index == 0 else "")
                if problem:
                    label += f"\u26a0 {problem} \u00b7 "
                label += os.path.basename(path)
                entry = QListWidgetItem(label)
                entry.setData(Qt.UserRole + 1, index == 0)
                if index == 0:
                    font = entry.font()
                    font.setBold(True)
                    entry.setFont(font)
                    entry.setForeground(QColor(self.pal["ACCENT_HOVER"]))
                    entry.setBackground(QColor(self.pal["PANEL_3"]))
                thumbnail = _thumb_pixmap(path, 56)
                if thumbnail:
                    entry.setIcon(QIcon(thumbnail))
                entry.setData(Qt.UserRole, path)
                entry.setToolTip(
                    f"{problem}: {path}" if problem else str(path))
                photo_list.addItem(entry)
            count_label.setText(f"??????????: {len(images)} ? ??????? ?????????? ???????? ?")
            if photo_list.count():
                photo_list.setCurrentRow(min(max(select, 0), photo_list.count() - 1))
            else:
                update_preview(-1)
            update_buttons()

        add_button = QPushButton("\u2795 \u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u0444\u043e\u0442\u043e", dialog)
        remove_button = QPushButton("???????", dialog)
        rename_button = QPushButton("?????????????", dialog)
        primary_button = QPushButton("??????? ???????", dialog)
        close_button = QPushButton("???????", dialog)
        row = QHBoxLayout()
        for button in (add_button, remove_button, rename_button, primary_button, close_button):
            row.addWidget(button)
        layout.addLayout(row)

        def update_buttons(*_):
            index = photo_list.currentRow()
            count = len(item.get("images") or [])
            valid = 0 <= index < count
            remove_button.setEnabled(valid)
            rename_button.setEnabled(valid)
            is_primary = valid and index == 0
            primary_button.setEnabled(valid and not is_primary)
            primary_button.setText(
                "\u2713 \u0413\u043b\u0430\u0432\u043d\u0430\u044f"
                if is_primary else
                "\u2605 \u0421\u0434\u0435\u043b\u0430\u0442\u044c \u0433\u043b\u0430\u0432\u043d\u043e\u0439")

        def add_photos(selected_files=None):
            if selected_files is None:
                selected_files = self._ask_image_files(
                    "\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u0444\u043e\u0442\u043e")
            if not selected_files:
                return

            current_images = list(item.get("images") or (
                [item.get("image")] if item.get("image") else []))
            added_images = []
            try:
                for source_path in selected_files:
                    relative_path = next_image_path(item.get("name", "item"), ".png")
                    # Track the destination before copying so a partially written
                    # file is also removed if the converter raises an exception.
                    added_images.append(relative_path)
                    copy_image_as_png(source_path, relative_path)
            except Exception as exc:
                for relative_path in added_images:
                    try:
                        full_path = absolute_path(relative_path)
                        if full_path and os.path.isfile(full_path):
                            os.remove(full_path)
                    except OSError:
                        pass
                QMessageBox.critical(
                    dialog, "\u041e\u0448\u0438\u0431\u043a\u0430 \u0434\u043e\u0431\u0430\u0432\u043b\u0435\u043d\u0438\u044f \u0444\u043e\u0442\u043e", str(exc))
                return

            updated_images = current_images + added_images
            item["images"] = updated_images
            item["image"] = updated_images[0] if updated_images else ""
            if not self._after_change(
                    "\u2713 \u0414\u043e\u0431\u0430\u0432\u043b\u0435\u043d\u043e \u0444\u043e\u0442\u043e: " + str(len(added_images)),
                    rollback_files=added_images):
                restored_item = self._find_item(item.get("id"))
                if restored_item is not None:
                    item.update(restored_item)
                refresh(len(item.get("images") or []))
                return

            self._show_selected()
            refresh(len(updated_images) - 1)

        def remove_photo():
            index = photo_list.currentRow()
            images = item.get("images") or []
            if not 0 <= index < len(images):
                return
            path = images[index]
            if QMessageBox.question(dialog, "??????? ????", f"??????? {os.path.basename(path)}?") != QMessageBox.Yes:
                return
            images.pop(index)
            item["images"] = images
            item["image"] = images[0] if images else ""
            if not self._after_change(delete_files=(path,)):
                restored_item = self._find_item(item.get("id"))
                if restored_item is not None:
                    item.update(restored_item)
                refresh(index)
                return
            self._show_selected()
            refresh(index)

        def rename_photo():
            index = photo_list.currentRow()
            images = item.get("images") or []
            if not 0 <= index < len(images):
                return
            path = images[index]
            current = os.path.splitext(os.path.basename(path))[0]
            base = self._ask_text("????????????? ????", "????? ???:", current)
            if base is None:
                return
            new_path = next_image_path(
                base.strip() or current, os.path.splitext(path)[1] or ".png")
            try:
                _move_photo_safely(absolute_path(path), absolute_path(new_path))
            except OSError as exc:
                QMessageBox.critical(dialog, "????", str(exc))
                return
            images[index] = new_path
            item["images"] = images
            item["image"] = images[0] if images else ""
            if not self._after_change(restore_files=((new_path, path),)):
                restored_item = self._find_item(item.get("id"))
                if restored_item is not None:
                    item.update(restored_item)
                refresh(index)
                return
            self._show_selected()
            refresh(index)

        def make_primary():
            index = photo_list.currentRow()
            images = item.get("images") or []
            if not 0 <= index < len(images) or index == 0:
                return
            images.insert(0, images.pop(index))
            item["images"] = images
            item["image"] = images[0]
            if self._after_change():
                self._show_selected()
                refresh(0)
                return

            restored_item = self._find_item(item.get("id"))
            if restored_item is not None:
                item.update(restored_item)
            refresh(0)

        photo_list.files_dropped = add_photos
        photo_list.setToolTip(
            "?????????? ?????????? ?????, ????? ???????? ???????. "
            "?????? ?????????? ????????? ???????.\n"
            "\u041f\u0435\u0440\u0435\u0442\u0430\u0449\u0438\u0442\u0435 PNG/JPG/WebP/BMP-\u0444\u0430\u0439\u043b\u044b \u0441\u044e\u0434\u0430.")
        photo_list.currentRowChanged.connect(update_preview)
        photo_list.currentRowChanged.connect(update_buttons)
        photo_list.model().rowsMoved.connect(save_dragged_order)
        add_button.clicked.connect(lambda _checked=False: add_photos())
        remove_button.clicked.connect(remove_photo)
        rename_button.clicked.connect(rename_photo)
        primary_button.clicked.connect(make_primary)
        close_button.clicked.connect(dialog.accept)
        refresh()
        dialog.exec()

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
        images.pop(idx)
        item["images"] = images
        item["image"] = images[0] if images else ""
        if not self._after_change(delete_files=(path,)):
            restored_item = self._find_item(item.get("id"))
            if restored_item is not None:
                item.update(restored_item)
            return
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
            _move_photo_safely(absolute_path(path), absolute_path(nr))
            images[idx] = nr
            item["images"] = images
            item["image"] = images[0] if images else ""
            if not self._after_change(restore_files=((nr, path),)):
                return
            self._show_selected()
        except OSError as e:
            QMessageBox.critical(self, "????", str(e))

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


class UnknownItemsView(QWidget):
    """Sequentially review unknown icons without interrupting scans."""

    def __init__(self, parent, reader, save_db, on_log=None):
        super().__init__(parent)
        self.reader = reader
        self._save_db = save_db
        self._on_log = on_log
        self.queue = load_unknown_queue(paths.UNKNOWN_QUEUE_FILE)
        self._build_review_ui()
        self.refresh_queue()

    def _build_review_ui(self):
        root = QHBoxLayout(self)
        left = QVBoxLayout()
        self.count_label = QLabel()
        left.addWidget(self.count_label)
        self.queue_list = QListWidget()
        self.queue_list.setToolTip("Очередь отсортирована по числу повторений; сначала идут самые частые")
        self.queue_list.currentRowChanged.connect(self._show_current)
        left.addWidget(self.queue_list, 1)
        queue_actions = QHBoxLayout()
        self.next_button = QPushButton("Следующий")
        self.next_button.clicked.connect(self._select_next)
        self.dismiss_button = QPushButton("Удалить из очереди")
        self.dismiss_button.clicked.connect(self._dismiss_current)
        queue_actions.addWidget(self.next_button)
        queue_actions.addWidget(self.dismiss_button)
        left.addLayout(queue_actions)
        root.addLayout(left, 1)

        right = QVBoxLayout()
        self.preview = QLabel("\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u043d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u044b\u0439 \u043f\u0440\u0435\u0434\u043c\u0435\u0442")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(180, 180)
        self.preview.setStyleSheet(
            f"background: {DEFAULT_PALETTE['PANEL_2']};"
            f" border: 1px solid {DEFAULT_PALETTE['BORDER']}; border-radius: 10px;")
        right.addWidget(self.preview, 1)
        self.meta_label = QLabel("")
        self.meta_label.setWordWrap(True)
        right.addWidget(self.meta_label)
        right.addWidget(QLabel("\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u043f\u0440\u0435\u0434\u043c\u0435\u0442\u0430"))
        self.name_edit = QLineEdit()
        self.name_edit.returnPressed.connect(self._promote_current)
        self.name_edit.setPlaceholderText("\u041d\u0430\u043f\u0440\u0438\u043c\u0435\u0440: \u0410\u043b\u043c\u0430\u0437\u043d\u044b\u0439 \u043c\u0435\u0447")
        right.addWidget(self.name_edit)
        right.addWidget(QLabel("\u0420\u0430\u0437\u043c\u0435\u0440 \u0441\u0442\u0430\u043a\u0430"))
        self.stack_combo = QComboBox()
        self.stack_combo.setMinimumWidth(120)
        self.stack_combo.setMaximumWidth(180)
        self.stack_combo.addItem("64 \u0448\u0442\u0443\u043a\u0438", 64)
        self.stack_combo.addItem("16 \u0448\u0442\u0443\u043a", 16)
        self.stack_combo.addItem("\u0421\u0432\u043e\u0451 \u0447\u0438\u0441\u043b\u043e", "custom")
        self.stack_custom = QSpinBox()
        self.stack_custom.setRange(1, 999999)
        self.stack_custom.setValue(64)
        self.stack_custom.setEnabled(False)
        self.stack_combo.currentIndexChanged.connect(
            lambda _i: self.stack_custom.setEnabled(self.stack_combo.currentData() == "custom"))
        right.addWidget(self.stack_combo)
        right.addWidget(self.stack_custom)
        right.addWidget(QLabel("\u041a\u0430\u0442\u0435\u0433\u043e\u0440\u0438\u044f"))
        self.category_combo = QComboBox()
        self.category_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.category_combo.setMinimumContentsLength(8)
        self.category_combo.setMinimumWidth(120)
        self.category_combo.setMaximumWidth(180)
        for key, title in paths.ITEM_CATEGORIES:
            self.category_combo.addItem(title, key)
        right.addWidget(self.category_combo)
        self.add_button = QPushButton(
            "\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u0432 \u0431\u0430\u0437\u0443")
        self.add_button.setObjectName("accent")
        self.add_button.setToolTip("Enter ? \u0434\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u043f\u0440\u0435\u0434\u043c\u0435\u0442 \u0438 \u043f\u0435\u0440\u0435\u0439\u0442\u0438 \u043a \u0441\u043b\u0435\u0434\u0443\u044e\u0449\u0435\u043c\u0443")
        self.add_button.clicked.connect(self._promote_current)
        right.addWidget(self.add_button)
        root.addLayout(right, 1)

    def refresh_queue(self, select_row=0):
        self.queue = load_unknown_queue(paths.UNKNOWN_QUEUE_FILE)
        self.queue_list.clear()
        entries = self.queue.get("items", [])
        entries.sort(key=lambda entry: (
            -int(entry.get("occurrences", 1) or 1),
            str(entry.get("last_seen", ""))), reverse=False)
        for entry in entries:
            count = max(1, int(entry.get("occurrences", 1) or 1))
            self.queue_list.addItem(
                f"{entry.get('id', 'unknown')}  ·  {count} "
                f"\u0432\u0441\u0442\u0440\u0435\u0447.")
        self.count_label.setText(
            f"\u041e\u0436\u0438\u0434\u0430\u044e\u0442 \u0440\u0430\u0437\u0431\u043e\u0440\u0430: {len(entries)}")
        self.next_button.setEnabled(bool(entries))
        self.dismiss_button.setEnabled(bool(entries))
        if entries:
            self.queue_list.setCurrentRow(min(max(select_row, 0), len(entries) - 1))
        else:
            self._show_current(-1)

    def _show_current(self, row):
        entries = self.queue.get("items", [])
        if row < 0 or row >= len(entries):
            self.preview.setPixmap(QPixmap())
            self.preview.setText("\u041e\u0447\u0435\u0440\u0435\u0434\u044c \u043f\u0443\u0441\u0442\u0430")
            self.meta_label.clear()
            self.name_edit.clear()
            self.add_button.setEnabled(False)
            self.next_button.setEnabled(False)
            self.dismiss_button.setEnabled(False)
            return
        self.next_button.setEnabled(len(entries) > 1)
        self.dismiss_button.setEnabled(True)
        entry = entries[row]
        path = absolute_path(entry.get("image"))
        pixmap = QPixmap(path) if path and os.path.isfile(path) else QPixmap()
        if pixmap.isNull():
            self.preview.setPixmap(QPixmap())
            self.preview.setText("\u0418\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u0435 \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u043e")
        else:
            self.preview.setText("")
            self.preview.setPixmap(pixmap.scaled(
                220, 220, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.meta_label.setText(
            f"ID: {entry.get('id', '')}\n"
            f"\u0412\u0441\u0442\u0440\u0435\u0447\u0430\u043b\u0441\u044f \u043f\u0440\u0438 \u0441\u043a\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0438: {entry.get('occurrences', 1)} \u0440\u0430\u0437\n"
            f"\u0412\u043f\u0435\u0440\u0432\u044b\u0435: {entry.get('first_seen', '-')}\n"
            f"\u041f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0439 \u0440\u0430\u0437: {entry.get('last_seen', '-')}")
        self.name_edit.clear()
        self.add_button.setEnabled(True)

    def _select_next(self):
        count = self.queue_list.count()
        if count < 2:
            return
        self.queue_list.setCurrentRow((self.queue_list.currentRow() + 1) % count)

    def _dismiss_current(self):
        row = self.queue_list.currentRow()
        entries = self.queue.get("items", [])
        if row < 0 or row >= len(entries):
            return
        answer = QMessageBox.question(
            self, "Удалить неизвестный предмет?",
            "Запись и связанное изображение будут удалены. Продолжить?")
        if answer != QMessageBox.Yes:
            return
        selected = entries[row]
        selected_id = selected.get("id")
        with profile_data_guard():
            queue = load_unknown_queue(paths.UNKNOWN_QUEUE_FILE)
            live_entries = queue.get("items", [])
            live_row = next((index for index, item in enumerate(live_entries)
                             if ((selected_id is not None and item.get("id") == selected_id)
                                 or (selected_id is None and item == selected))), None)
            if live_row is None:
                self.queue = queue
                self.refresh_queue()
                return
            removed = live_entries.pop(live_row)
            try:
                save_json(paths.UNKNOWN_QUEUE_FILE, queue, backup=True)
            except Exception as exc:
                live_entries.insert(live_row, removed)
                QMessageBox.critical(self, "?? ??????? ????????? ???????", str(exc))
                return
            image_path = absolute_path(removed.get("image", ""))
            if image_path and os.path.isfile(image_path):
                try:
                    os.remove(image_path)
                except OSError as exc:
                    if self._on_log:
                        self._on_log(f"?? ??????? ??????? ???? ???????????: {exc}", False)
            self.queue = queue
        self.refresh_queue(select_row=min(live_row, len(live_entries) - 1))
        if self._on_log:
            self._on_log("??????????? ??????? ?????? ?? ???????.", True)

    def _promote_current(self):
        row = self.queue_list.currentRow()
        entries = self.queue.get("items", [])
        if row < 0 or row >= len(entries):
            return
        selected = entries[row]
        selected_id = selected.get("id")
        entered_name = self.name_edit.text()
        with profile_data_guard():
            self.queue = load_unknown_queue(paths.UNKNOWN_QUEUE_FILE)
            self.refresh_queue(select_row=0)
            fresh_entries = self.queue.get("items", [])
            fresh_row = next((index for index, item in enumerate(fresh_entries)
                              if ((selected_id is not None and item.get("id") == selected_id)
                                  or (selected_id is None and item == selected))), None)
            if fresh_row is None:
                self.refresh_queue()
                return
            self.queue_list.setCurrentRow(fresh_row)
            # Refreshing the selected row clears the name field; preserve input.
            self.name_edit.setText(entered_name)
            self._promote_current_locked()

    def _promote_current_locked(self):
        row = self.queue_list.currentRow()
        entries = self.queue.get("items", [])
        if row < 0 or row >= len(entries):
            return
        entry = entries[row]
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.information(
                self, "\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u043f\u0440\u0435\u0434\u043c\u0435\u0442\u0430",
                "\u0423\u043a\u0430\u0436\u0438 \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u0435, \u0447\u0442\u043e\u0431\u044b \u0434\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u043f\u0440\u0435\u0434\u043c\u0435\u0442 \u0432 \u0431\u0430\u0437\u0443.")
            self.name_edit.setFocus()
            return
        source_rel = entry.get("image", "")
        source = absolute_path(source_rel)
        if not source or not os.path.isfile(source):
            QMessageBox.warning(
                self, "\u041d\u0435\u0442 \u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u044f",
                "\u0424\u0430\u0439\u043b \u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u044f \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d. \u0417\u0430\u043f\u0438\u0441\u044c \u043e\u0441\u0442\u0430\u0432\u043b\u0435\u043d\u0430 \u0432 \u043e\u0447\u0435\u0440\u0435\u0434\u0438.")
            return
        selected_stack = self.stack_combo.currentData()
        stack = self.stack_custom.value() if selected_stack == "custom" else int(selected_stack)
        category = self.category_combo.currentData() or "uncategorized"
        db = self.reader.items_db
        existing = next((x for x in db.get("items", [])
                         if x.get("name", "").casefold() == name.casefold()), None)
        target_rel = next_image_path(name, ".png")
        target = absolute_path(target_rel)
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if existing is None:
                shutil.copy2(source, target)
                item = {"id": str(uuid.uuid4()), "name": name, "stack_size": stack,
                        "images": [target_rel], "image": target_rel,
                        "enabled": True, "category": category}
                db.setdefault("items", []).append(item)
            else:
                images = list(existing.get("images") or
                              ([existing.get("image")] if existing.get("image") else []))
                already_added = any(
                    candidate and os.path.isfile(absolute_path(candidate))
                    and filecmp.cmp(source, absolute_path(candidate), shallow=False)
                    for candidate in images)
                if not already_added:
                    shutil.copy2(source, target)
                    images.append(target_rel)
                existing["images"] = images
                existing["image"] = images[0] if images else ""
                existing["stack_size"] = stack
            self._save_db(db)
            removed = entries.pop(row)
            try:
                save_json(paths.UNKNOWN_QUEUE_FILE, self.queue, backup=True)
            except Exception:
                entries.insert(row, removed)
                raise
        except Exception as exc:
            QMessageBox.critical(
                self, "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0434\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u043f\u0440\u0435\u0434\u043c\u0435\u0442", str(exc))
            return
        if self._on_log:
            self._on_log(
                f"\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u044b\u0439 \u043f\u0440\u0435\u0434\u043c\u0435\u0442 \u0434\u043e\u0431\u0430\u0432\u043b\u0435\u043d \u0432 \u0431\u0430\u0437\u0443: {name}", True)
        self.refresh_queue(select_row=row)
