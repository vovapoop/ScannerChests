"""Интерактивный тур: затемнение интерфейса, «прожектор» на элементе и карточка шага.

Используется для обучения в окне настроек: каждый шаг подсвечивает карточку
или кнопку и объясняет, что с ней делать. Подсвеченная область не перекрыта
слоем, поэтому в ней можно работать с интерфейсом. Шаг может требовать
результата (require): пока условие не выполнено, «Далее» заблокирована.
"""
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QRegion
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from chest_scanner.gui.theme import PALETTE

#: Стиль карточки шага (полупрозрачный слой красится вручную в paintEvent).
_CARD_QSS = f"""
QWidget#tourCard {{
    background: {PALETTE["PANEL_2"]};
    border: 1px solid {PALETTE["ACCENT"]};
    border-radius: 10px;
}}
QLabel#tourTitle {{
    color: {PALETTE["TEXT"]};
    font-size: 15px;
    font-weight: 600;
}}
QLabel#tourText {{
    color: {PALETTE["TEXT"]};
    font-size: 13px;
}}
QLabel#tourCounter {{
    color: {PALETTE["MUTED"]};
    font-size: 11px;
}}
QLabel#tourHint {{
    color: {PALETTE["YELLOW"]};
    font-size: 12px;
}}
QLabel#tourNote {{
    color: {PALETTE["MUTED"]};
    font-size: 12px;
}}
"""

#: Отступ «прожектора» вокруг целевого виджета, px.
HOLE_PAD = 10

_QPOINT_ZERO = QPoint(0, 0)

#: Минимальный зазор карточки от «прожектора» и краёв окна, px.
CARD_MARGIN = 16


@dataclass
class TourStep:
    """Шаг тура: заголовок, текст и необязательная цель для подсветки."""

    title: str
    text: str
    target: QWidget = None
    #: Колбэк перед показом шага (переключение страницы, прокрутка).
    before: object = None
    #: Условие шага: пока возвращает False, «Далее» и «Пропустить» недоступны.
    require: object = None
    #: Подсказка под текстом, пока условие не выполнено.
    hint: str = ""
    #: Кнопка действия в карточке: (подпись, колбэк). Колбэк может вернуть
    #: строку — она показывается под текстом шага.
    action: object = None


class TourOverlay(QWidget):
    """Полупрозрачный слой поверх окна с «прожектором» и карточкой шага."""

    def __init__(self, host, steps, on_finish=None):
        super().__init__(host)
        self._host = host
        self._steps = list(steps)
        self._idx = 0
        self._on_finish = on_finish
        self.setGeometry(host.rect())
        self.setStyleSheet(_CARD_QSS)
        host.installEventFilter(self)

        self._card = QWidget(self)
        self._card.setObjectName("tourCard")
        lay = QVBoxLayout(self._card)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(8)
        self._title_lbl = QLabel()
        self._title_lbl.setObjectName("tourTitle")
        self._text_lbl = QLabel()
        self._text_lbl.setObjectName("tourText")
        self._text_lbl.setWordWrap(True)
        self._counter_lbl = QLabel()
        self._counter_lbl.setObjectName("tourCounter")
        self._counter_lbl.setAlignment(Qt.AlignRight)

        self._hint_lbl = QLabel()
        self._hint_lbl.setObjectName("tourHint")
        self._hint_lbl.setWordWrap(True)
        self._hint_lbl.setVisible(False)

        self._note_lbl = QLabel()
        self._note_lbl.setObjectName("tourNote")
        self._note_lbl.setWordWrap(True)
        self._note_lbl.setVisible(False)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        back = QPushButton("Назад")
        back.clicked.connect(self.back)
        self._next_btn = QPushButton("Далее")
        self._next_btn.clicked.connect(self.next)
        self._skip_btn = QPushButton("Пропустить")
        self._skip_btn.clicked.connect(self.finish)
        #: Кнопка действия шага (например, автокалибровка сетки).
        self._action_btn = QPushButton()
        self._action_btn.setVisible(False)
        self._action_btn.clicked.connect(self._run_action)
        btns.addWidget(back)
        btns.addWidget(self._next_btn)
        btns.addWidget(self._action_btn)
        btns.addStretch(1)
        btns.addWidget(self._skip_btn)
        lay.addWidget(self._title_lbl)
        lay.addWidget(self._text_lbl)
        lay.addWidget(self._hint_lbl)
        lay.addWidget(self._note_lbl)
        lay.addWidget(self._counter_lbl)
        lay.addLayout(btns)

        self._last_pending = None
        self._check_timer = QTimer(self)
        self._check_timer.setInterval(300)
        self._check_timer.timeout.connect(self._sync_require)
        self._check_timer.start()

        self.setFocusPolicy(Qt.StrongFocus)
        self.show()
        self.raise_()
        self.setFocus()
        self._apply()

    # ── Навигация ──
    def _step(self):
        return self._steps[self._idx]

    def next(self):
        if self._pending():
            self._sync_require()
            return
        if self._idx >= len(self._steps) - 1:
            self.finish()
            return
        self._idx += 1
        self._apply()

    def back(self):
        if self._idx > 0:
            self._idx -= 1
            self._apply()

    def _run_action(self):
        """Выполняет действие шага и показывает его результат под текстом."""
        action = self._step().action
        if not action or len(action) < 2 or action[1] is None:
            return
        self._action_btn.setEnabled(False)
        self.set_note("Выполняется…")
        # Долгая операция блокирует цикл событий — даём Qt перерисовать подпись.
        QApplication.processEvents()
        try:
            message = action[1]()
        except Exception as e:
            message = f"Не получилось: {e}"
        finally:
            self._action_btn.setEnabled(True)
        self.set_note(message)
        self._last_pending = None
        self._sync_require()
        self._place_card()
        self._apply_mask()

    def set_note(self, text):
        """Пояснение под текстом шага (результат действия)."""
        text = str(text or "")
        self._note_lbl.setText(text)
        self._note_lbl.setVisible(bool(text))
        self._place_card()

    def finish(self):
        self._check_timer.stop()
        self._host.removeEventFilter(self)
        if self._on_finish is not None:
            self._on_finish()
        self.deleteLater()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            if not self._pending():
                self.finish()
            return
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Right):
            self.next()
        elif ev.key() == Qt.Key_Left:
            self.back()
        else:
            super().keyPressEvent(ev)

    # ── Условия шага ──
    def _pending(self):
        """Шаг ещё не выполнен: условие require не достигнуто."""
        req = self._step().require
        if req is None:
            return False
        try:
            return not bool(req())
        except Exception:
            return False

    def _sync_require(self):
        """Перепроверяет условие шага и обновляет кнопки и подсказку."""
        if getattr(self, "_host", None) is None or not self.isVisible():
            return
        if not self._steps:
            return
        pending = self._pending()
        if pending == self._last_pending:
            return
        self._last_pending = pending
        self._next_btn.setEnabled(not pending)
        self._skip_btn.setVisible(not pending)
        hint = self._step().hint if pending else ""
        self._hint_lbl.setText(hint)
        self._hint_lbl.setVisible(bool(hint))
        self._place_card()

    # ── Маска: подсвеченная область остаётся интерактивной ──
    def _apply_mask(self):
        """Срезает подсвеченную область из слоя, чтобы клики доходили до неё."""
        region = QRegion(self.rect())
        hole = self._hole()
        if hole is not None:
            x, y, w, h = (int(round(v)) for v in hole)
            clipped = QRect(x, y, w, h).intersected(self.rect())
            if not clipped.isEmpty():
                region = region.subtracted(QRegion(clipped))
        card = self._card.geometry()
        if card.isValid() and not card.isEmpty():
            region = region.united(QRegion(card.intersected(self.rect())))
        if region.isEmpty():
            return
        self.setMask(region)

    def wheelEvent(self, ev):
        """Прокрутка страницы настроек колесом поверх затемнения."""
        for area in self._host.findChildren(QScrollArea):
            if area.isVisible():
                bar = area.verticalScrollBar()
                bar.setValue(bar.value() - ev.angleDelta().y())
                return
        super().wheelEvent(ev)

    # ── Отрисовка и раскладка ──
    def _hole(self):
        """Прямоугольник подсветки цели в координатах слоя или None."""
        t = self._step().target
        if t is None or not t.isVisible():
            return None
        try:
            top_left = t.mapTo(self._host, _QPOINT_ZERO)
        except RuntimeError:
            return None
        return top_left.x() - HOLE_PAD, top_left.y() - HOLE_PAD, \
            t.width() + HOLE_PAD * 2, t.height() + HOLE_PAD * 2

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRect(QRectF(self.rect()))
        hole = self._hole()
        if hole is not None:
            hole_path = QPainterPath()
            hole_path.addRoundedRect(QRectF(*hole), 10, 10)
            path = path.subtracted(hole_path)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(6, 9, 14, 180))
        p.drawPath(path)
        if hole is not None:
            pen = p.pen()
            pen.setColor(QColor(PALETTE["ACCENT"]))
            pen.setWidth(2)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            # Рамка чуть снаружи «окна»: само окно вырезано маской и не рисуется.
            p.drawRoundedRect(QRectF(*hole).adjusted(-4, -4, 4, 4), 12, 12)

    def eventFilter(self, obj, ev):
        # Фильтр может сработать уже после удаления слоя — проверяем состояние.
        host = getattr(self, "_host", None)
        if host is None:
            return False
        if obj is host and ev.type() == ev.Type.Resize:
            self.setGeometry(host.rect())
            self._place_card()
        return super().eventFilter(obj, ev)

    def _apply(self):
        """Готовит и показывает текущий шаг: колбэк, подписи, позиция."""
        step = self._step()
        if step.before is not None:
            try:
                step.before()
            except Exception:
                pass
            # Ждём, пока Qt перестроит страницу — иначе цель не найдена.
            QApplication.processEvents()
        self._title_lbl.setText(step.title)
        self._text_lbl.setText(step.text)
        self._counter_lbl.setText(f"Шаг {self._idx + 1} из {len(self._steps)}")
        self._next_btn.setText("Готово" if self._idx == len(self._steps) - 1
                               else "Далее")
        self._note_lbl.clear()
        self._note_lbl.setVisible(False)
        action = step.action
        self._action_btn.setVisible(bool(action))
        if action:
            self._action_btn.setText(str(action[0]))
        self._last_pending = None
        self._place_card()
        self._sync_require()
        self._apply_mask()
        self.raise_()
        self.setFocus()

    def _place_card(self):
        """Ставит карточку рядом с целью (ниже, выше) или по центру."""
        self._card.adjustSize()
        cw = min(max(self._card.width(), 340), max(280, self.width() - CARD_MARGIN * 2))
        ch = self._card.height()
        hole = self._hole()
        if hole is None:
            x = (self.width() - cw) // 2
            y = (self.height() - ch) // 2
        else:
            hx, hy, hw, _hh = hole
            x = min(max(hx, CARD_MARGIN), max(CARD_MARGIN, self.width() - cw - CARD_MARGIN))
            y = hy + _hh + CARD_MARGIN
            if y + ch > self.height() - CARD_MARGIN:
                y = max(CARD_MARGIN, hy - CARD_MARGIN - ch)
        self._card.setGeometry(max(CARD_MARGIN, x), max(CARD_MARGIN, y), cw, ch)
        self._apply_mask()
