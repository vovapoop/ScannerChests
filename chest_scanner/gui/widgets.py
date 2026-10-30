"""GUI widget: переиспользуемые виджеты и хелперы на PySide6."""
import os
import shutil

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QParallelAnimationGroup,
    QPropertyAnimation,
    Qt,
    QTimer,
)
from PySide6.QtGui import QCursor, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from chest_scanner import paths
from chest_scanner.commons import absolute_path
from chest_scanner.gui.theme import PALETTE
from chest_scanner.items import sanitize_filename


class HoverButton(QPushButton):
    """Кнопка в фирменном стиле: accent/danger/ghost через objectName."""

    def __init__(self, text="", parent=None, accent=False, danger=False,
                 ghost=False, min_height=34):
        super().__init__(text, parent)
        if accent:
            self.setObjectName("accent")
        elif danger:
            self.setObjectName("danger")
        elif ghost:
            self.setObjectName("ghost")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(min_height)
        self.setFocusPolicy(Qt.NoFocus)


class StatCard(QFrame):
    """Карточка статистики: заголовок и крупное значение."""

    def __init__(self, title, value="0", color=None, parent=None):
        super().__init__(parent)
        self.setProperty("card", True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(3)
        t = QLabel(title.upper())
        t.setProperty("section", True)
        lay.addWidget(t)
        self.value_lbl = QLabel(value)
        self.value_lbl.setProperty("big", True)
        if color:
            self.value_lbl.setStyleSheet(f"color: {color};")
        lay.addWidget(self.value_lbl)

    def set_value(self, value):
        self.value_lbl.setText(str(value))


def make_label(text, muted=False, small=False, big=False, section=False,
               title=False, color=None, wrap=False):
    """Хелпер для QLabel с типовыми свойствами стиля."""
    lbl = QLabel(text)
    lbl.setProperty("muted", muted)
    lbl.setProperty("small", small)
    lbl.setProperty("big", big)
    lbl.setProperty("section", section)
    lbl.setProperty("title", title)
    if color:
        lbl.setStyleSheet(f"color: {color};")
    if wrap:
        lbl.setWordWrap(True)
    return lbl


class SettingCard(QFrame):
    """Карточка для страниц настроек с заголовком и подзаголовком."""

    def __init__(self, title, subtitle=None, parent=None):
        super().__init__(parent)
        self.setProperty("card", True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 18)
        lay.setSpacing(2)
        t = make_label(title, big=True)
        lay.addWidget(t)
        if subtitle:
            s = make_label(subtitle, muted=True, wrap=True)
            lay.addWidget(s)
        self.content = QVBoxLayout()
        self.content.setSpacing(6)
        lay.addLayout(self.content)
        lay.addStretch(1)


def entry_row(parent_layout, label, widget, stretch=None):
    """Классическая строка «подпись слева — виджет справа»."""
    row = QHBoxLayout()
    row.setSpacing(10)
    lb = make_label(label, muted=True)
    row.addWidget(lb, 1)
    row.addWidget(widget, stretch if stretch is not None else 0)
    parent_layout.addLayout(row)
    return row


class TemplateSelector(QWidget):
    """Выбор файла-шаблона с предпросмотром изображения.

    Сигнал changed(str) — выбран относительный путь (или пустая строка).
    """

    def __init__(self, value="", title="Выбор файла", parent=None):
        super().__init__(parent)
        pal = PALETTE
        self._title = title
        self._preview_pixmap = None

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)

        self.preview = QLabel("Нет файла")
        self.preview.setProperty("muted", True)
        self.preview.setFixedSize(92, 92)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet(
            f"background: {pal['PANEL_2']}; border: 1px solid {pal['BORDER']};"
            " border-radius: 8px;")
        row.addWidget(self.preview, 0, Qt.AlignTop)

        info = QVBoxLayout()
        info.setSpacing(8)
        self.path_lbl = make_label(value or "—", muted=True, wrap=True)
        info.addWidget(self.path_lbl)
        choose_btn = HoverButton("Выбрать файл...", accent=True, min_height=32)
        choose_btn.clicked.connect(self._choose)
        info.addWidget(choose_btn)
        info.addStretch(1)
        row.addLayout(info, 1)

        self._value = value
        self._update_preview(value)

    # ── API ──
    def value(self):
        return self._value

    def set_value(self, value):
        self._value = value or ""
        self.path_lbl.setText(self._value or "—")
        self._update_preview(self._value)

    # ── Внутреннее ──
    def _choose(self):
        start = ""
        full = absolute_path(self._value)
        if full and os.path.isfile(full):
            start = os.path.dirname(full)
        path, _ = QFileDialog.getOpenFileName(
            self, self._title, start,
            "Изображения (*.png *.jpg *.jpeg);;Все файлы (*.*)")
        if not path:
            return
        rel = self._import_template_file(path, os.path.basename(path))
        self.set_value(rel)

    def _import_template_file(self, source, default_name):
        sa = os.path.abspath(source)
        ba = os.path.abspath(paths.BASE_DIR)
        try:
            if os.path.commonpath([sa, ba]) == ba:
                return os.path.relpath(sa, ba).replace("\\", "/")
        except ValueError:
            pass
        cd = os.path.join(paths.ASSETS_DIR, "custom")
        os.makedirs(cd, exist_ok=True)
        name, ext = os.path.splitext(sanitize_filename(default_name or "template"))
        ext = ext.lower() if ext.lower() in (".png", ".jpg", ".jpeg") else ".png"
        tn = f"{name}{ext}"
        ta = os.path.join(cd, tn)
        c = 1
        while os.path.exists(ta):
            tn = f"{name}_{c}{ext}"
            ta = os.path.join(cd, tn)
            c += 1
        shutil.copy2(sa, ta)
        return os.path.relpath(ta, ba).replace("\\", "/")

    def _update_preview(self, path):
        self.preview.setText("Нет файла")
        self._preview_pixmap = None
        full = absolute_path(path)
        if not full or not os.path.isfile(full):
            return
        try:
            pm = QPixmap(full)
            if pm.isNull():
                return
            pm = pm.scaled(88, 88, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._preview_pixmap = pm
            self.preview.setPixmap(pm)
        except Exception:
            self.preview.setText("Нет предпросмотра")


class SideBar(QFrame):
    """Боковая панель навигации с автоскрытием.

    По умолчанию панель свёрнута в узкую полосу с иконками; при наведении
    курсора плавно разворачивается, при уходе — снова сворачивается.

    tools — список кортежей (ключ, «иконка  подпись», обработчик).
    """

    WIDTH_FULL = 200
    WIDTH_COLLAPSED = 44

    def __init__(self, tools, on_exit=None, parent=None):
        super().__init__(parent)
        self.setProperty("card", True)
        self.setMinimumWidth(self.WIDTH_COLLAPSED)
        self.setMaximumWidth(self.WIDTH_COLLAPSED)
        self._expanded = False
        self._anim = None

        self._build(tools, on_exit)
        self._sync_view()

        self._anim_target = None
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(90)
        self._hover_timer.timeout.connect(self._poll_hover)
        self._hover_timer.start()

        # Гистерезис: задержки вместо мгновенной реакции исключают «дёрганье».
        self._expand_delay = QTimer(self)
        self._expand_delay.setSingleShot(True)
        self._expand_delay.setInterval(120)
        self._expand_delay.timeout.connect(self._do_expand)
        self._collapse_delay = QTimer(self)
        self._collapse_delay.setSingleShot(True)
        self._collapse_delay.setInterval(350)
        self._collapse_delay.timeout.connect(self._do_collapse)

    # ── API ──
    def is_expanded(self):
        return self._expanded

    def set_expanded(self, expanded, animate=True):
        """Разворачивает/сворачивает панель с плавной анимацией ширины."""
        if expanded == self._expanded:
            return
        target = self.WIDTH_FULL if expanded else self.WIDTH_COLLAPSED
        if (self._anim is not None
                and self._anim.state() == QAbstractAnimation.Running
                and self._anim_target == target):
            self._expanded = expanded  # анимация уже идёт в нужную сторону
            return
        if self._anim is not None:
            self._anim.stop()
        self._expanded = expanded
        self._anim_target = target
        self._anim = QParallelAnimationGroup(self)
        # Важно: QWidget.minimumWidth()/maximumWidth() — это методы, а не свойства.
        starts = {b"minimumWidth": self.minimumWidth(),
                  b"maximumWidth": self.maximumWidth()}
        for prop in (b"minimumWidth", b"maximumWidth"):
            anim = QPropertyAnimation(self, prop, self)
            anim.setDuration(180 if animate else 0)
            anim.setStartValue(starts[prop])
            anim.setEndValue(target)
            anim.setEasingCurve(QEasingCurve.InOutCubic)
            self._anim.addAnimation(anim)
        self._anim.finished.connect(self._anim_finished)
        self._anim.start()
        self._sync_view()

    # ── Сборка ──
    def _build(self, tools, on_exit):
        pal = PALETTE
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 12, 8, 10)
        lay.setSpacing(2)

        self.section_lbl = make_label("ИНСТРУМЕНТЫ", section=True)
        lay.addWidget(self.section_lbl)
        lay.addSpacing(6)

        self.buttons = {}
        self._tool_meta = {}
        for key, text, cmd in tools:
            parts = text.split("  ", 1)
            icon = parts[0] if parts else ""
            label = parts[1] if len(parts) > 1 else ""
            b = HoverButton(text, ghost=True)
            b.clicked.connect(cmd)
            lay.addWidget(b)
            self.buttons[key] = b
            self._tool_meta[key] = (icon, label)

        lay.addStretch(1)

        self.divider = QFrame()
        self.divider.setFixedHeight(1)
        self.divider.setStyleSheet(f"background: {pal['BORDER']};")
        lay.addWidget(self.divider)

        self.exit_btn = HoverButton("✕  Выход", ghost=True)
        self.exit_btn.clicked.connect(on_exit or (lambda: None))
        lay.addWidget(self.exit_btn)

    # ── Поведение ──
    def _poll_hover(self):
        """Наведение/уход курсора с задержками — без резких переключений."""
        top = QApplication.widgetAt(QCursor.pos())
        over = top is not None and (top is self or self.isAncestorOf(top))
        if over:
            self._collapse_delay.stop()
            if self._expanded:
                return
            if not self._expand_delay.isActive():
                self._expand_delay.start()
        else:
            self._expand_delay.stop()
            # Не сворачиваем под открытым меню/диалогом — иначе панель «дёргается».
            if (QApplication.activePopupWidget() is not None
                    or QApplication.activeModalWidget() is not None):
                return
            if self._expanded and not self._collapse_delay.isActive():
                self._collapse_delay.start()

    def _do_expand(self):
        self.set_expanded(True)

    def _do_collapse(self):
        top = QApplication.widgetAt(QCursor.pos())
        over = top is not None and (top is self or self.isAncestorOf(top))
        if not self._expanded or over:
            return
        if self._anim is not None and self._anim.state() == QAbstractAnimation.Running:
            return  # не обрываем анимацию на середине: сработает на следующем цикле
        self.set_expanded(False)

    def _anim_finished(self):
        self._anim_target = None

    def _sync_view(self):
        """Обновляет тексты кнопок и видимость заголовков при смене состояния."""
        expanded = self._expanded
        self.section_lbl.setVisible(expanded)
        self.divider.setVisible(expanded)
        pad = "" if expanded else "padding: 6px 0px; text-align: center;"
        for key, (icon, label) in self._tool_meta.items():
            btn = self.buttons[key]
            btn.setText(f"{icon}  {label}" if expanded else icon)
            btn.setStyleSheet(pad)
        self.exit_btn.setText("✕  Выход" if expanded else "✕")
        self.exit_btn.setStyleSheet(pad)
