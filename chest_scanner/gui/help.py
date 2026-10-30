"""Встроенная справка «Как пользоваться» (диалог на QTextBrowser)."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QTextBrowser,
    QVBoxLayout,
)

from chest_scanner.gui.theme import PALETTE
from chest_scanner.gui.widgets import HoverButton, make_label

HELP_BG = "#10141d"
HELP_TEXT = "#c9ced6"
HELP_MUTED = "#8a94a6"
HELP_ACCENT = PALETTE["ACCENT"]
HELP_ACCENT_HOVER = PALETTE["ACCENT_HOVER"]
HELP_PANEL = "#1a2130"

_HELP_HTML = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family: 'Segoe UI', Arial, sans-serif; font-size: 13px;
             color: {HELP_TEXT}; background: {HELP_BG}; margin: 0; padding: 18px;">

<h2 id="start" style="color: {HELP_ACCENT}; margin-bottom: 4px;">Chest Scanner — краткая справка</h2>
<p style="color: {HELP_MUTED}; margin-top: 0;">Распознавание сундуков Minecraft по скриншоту: содержимое, количество, база предметов и отчёты.</p>

<h3 id="quick" style="color: {HELP_ACCENT_HOVER};">Быстрый старт</h3>
<ol>
  <li>Откройте сундук в игре — интерфейс сундука должен быть на экране.</li>
  <li>Нажмите <b>F8</b> (или кнопку «Сканировать»). Программа определит сетку, предметы и количество.</li>
  <li>Если сундук не распознан — выполните «Калибровку» на панели слева и при необходимости «Шаблоны (авто)».</li>
</ol>

<h3 id="hotkeys" style="color: {HELP_ACCENT_HOVER};">Горячие клавиши</h3>
<table cellpadding="4" style="border-collapse: collapse;">
  <tr><td style="color: {HELP_MUTED};">F6</td><td>— показать / скрыть окно</td></tr>
  <tr><td style="color: {HELP_MUTED};">F8</td><td>— сканирование сундука</td></tr>
  <tr><td style="color: {HELP_MUTED};">F9</td><td>— отправка отчёта в Discord</td></tr>
  <tr><td style="color: {HELP_MUTED};">F10</td><td>— выход из приложения</td></tr>
</table>
<p>Клавиши настраиваются в «Настройки → Основные → Горячие клавиши». Пустое поле
отключает действие. Изменения применяются сразу после «Сохранить», без перезапуска.
Глобальные хоткеи работают через WinAPI без прав администратора.</p>

<h3 id="scan" style="color: {HELP_ACCENT_HOVER};">Сканирование</h3>
<p>Во время сканирования в верхней панели отображается <b>прогресс обработки
ячеек</b> и кнопка <b>«Отмена»</b> — скан можно прервать в любой момент.</p>
<ul>
  <li>Предметы определяются по иконкам (pHash + точное сравнение), количество — по шаблонам цифр.</li>
  <li>Неизвестные иконки автоматически добавляются в базу как «unknown_…» — дайте им имя сразу в диалоге или позже в «Предметы».</li>
  <li>После скана приложение уведомляет о результате и количестве неизвестных предметов (в трее).</li>
</ul>

<h3 id="autoscan" style="color: {HELP_ACCENT_HOVER};">Автоскан</h3>
<p>Кнопка «▶ Автоскан» начинает периодическую проверку экрана: как только
открытый сундук меняет содержимое, делается новый скан автоматически.
Интервал настраивается в «Настройки → Основные». Автоскан можно включить
при запуске программы.</p>

<h3 id="calibration" style="color: {HELP_ACCENT_HOVER};">Калибровка сетки</h3>
<p>Если сетка не совпадает с сундуком:</p>
<ol>
  <li>«Сетка (авто)» на панели слева — программа сама найдёт границы сундука
      по его ячейкам и сохранит сетку. Сундук должен быть открыт, а его
      интерфейс виден целиком. Результат можно проверить окном «Сетка (отладка)».</li>
  <li>«Калибровка» — ручной способ: отметьте два угла области сундука кликами
      (или настройте координаты в «Настройки → Сетка»).</li>
  <li>«Шаблоны (авто)» — создаст шаблон открытого сундука и пустого слота из текущего экрана.</li>
  <li>Пресеты «Двойной сундук (9×6)» и «Одиночный сундук (9×3)» — в настройках сетки.</li>
  <li>При смене разрешения или масштаба Windows перекалибруйте сетку.</li>
</ol>
<p>Отладочные окна «Сетка» и «Цифры» (панель слева) показывают, что именно
программа видит на экране.</p>

<h3 id="items" style="color: {HELP_ACCENT_HOVER};">База предметов</h3>
<ul>
  <li>«Предметы» — просмотр и редактирование базы: категории, теги, алиасы, несколько фото, размер стака.</li>
  <li>Неизвестные иконки сохраняются в папку <span style="color: {HELP_MUTED};">unknown_items/</span> — их можно переименовывать.</li>
  <li>Базу можно переносить между компьютерами: «Данные → Экспорт/Импорт базы» (zip-архив без серверов).</li>
</ul>

<h3 id="history" style="color: {HELP_ACCENT_HOVER};">История и статистика</h3>
<p>«История» — все сканирования с фильтрами и сравнением двух сканов.
«Статистика» — сводные цифры по базе. Полная выгрузка — «Данные → Экспорт данных (CSV/JSON)».</p>

<h3 id="discord" style="color: {HELP_ACCENT_HOVER};">Discord</h3>
<p>Для отправки отчётов укажите URL webhook канала в «Настройки → Discord»
или задайте переменную окружения (имя настраивается там же). Формат сообщения — блок
«discord_message» в <span style="color: {HELP_MUTED};">config.json</span> профиля.</p>

<h3 id="troubleshooting" style="color: {HELP_ACCENT_HOVER};">Устранение неполадок</h3>
<table cellpadding="4" style="border-collapse: collapse;">
  <tr><td style="color: {HELP_MUTED};">Окно закрылось сразу после запуска</td><td>— смотрите startup_error.log и venv_setup.log в корне</td></tr>
  <tr><td style="color: {HELP_MUTED};">Хоткеи не работают</td><td>— проверьте поля в настройках; перезапустите приложение (хук пересоздаётся)</td></tr>
  <tr><td style="color: {HELP_MUTED};">Предметы не распознаются</td><td>— обновите базу в «Предметы», проверьте пороги в «Настройки → Распознавание»</td></tr>
  <tr><td style="color: {HELP_MUTED};">Сетка не совпадает</td><td>— выполните калибровку; при смене масштаба Windows — перекалибруйте</td></tr>
  <tr><td style="color: {HELP_MUTED};">Настройки «поплыли»</td><td>— «Сброс настроек» на панели слева (старый конфиг в .backup)</td></tr>
</table>

</body></html>"""


class HelpDialog(QDialog):
    """Окно встроенной справки: простая навигация и понятные тексты."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Справка — Chest Scanner")
        self.resize(780, 620)
        self.setMinimumSize(640, 480)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(12)
        root.addWidget(make_label("Справка", title=True))

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.browser.setStyleSheet(
            f"QTextBrowser {{ background: {HELP_BG}; color: {HELP_TEXT};"
            f" border: 1px solid {HELP_PANEL}; border-radius: 8px;"
            " padding: 6px; font-size: 13px; }}")
        self.browser.document().setDefaultStyleSheet(
            f"a {{ color: {HELP_ACCENT}; }}")
        self.browser.setHtml(_HELP_HTML)
        root.addWidget(self.browser, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        bottom.addWidget(make_label(
            "Подробнее о данных и структуре — в README.md проекта.", muted=True, small=True))
        bottom.addStretch(1)
        close_btn = HoverButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.accept()
            return
        super().keyPressEvent(event)
