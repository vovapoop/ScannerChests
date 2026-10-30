"""Главное окно Chest Scanner на PySide6 (Qt).

Заменило tkinter-миксины: логика та же (трей, журнал, горячие клавиши,
фоновые задачи через потоки), но обновление UI — через сигналы и QTimer.
"""
import html
import os
import shutil
import subprocess
import sys
import threading
import time
from queue import Empty, Queue

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from chest_scanner import paths
from chest_scanner.app_bootstrap import KEYBOARD_AVAILABLE, keyboard
from chest_scanner.commons import absolute_path, load_json, save_json
from chest_scanner.config import load_profiles
from chest_scanner.gui.dialogs import (
    ExportDialog,
    FirstRunWizard,
    HistoryDialog,
    ItemDatabaseDialog,
    ProfileManagerDialog,
    StatsDialog,
    UnknownItemDialog,
)
from chest_scanner.gui.help import HelpDialog
from chest_scanner.gui.settings import SettingsDialog
from chest_scanner.gui.theme import PALETTE, build_stylesheet
from chest_scanner.gui.widgets import HoverButton, SideBar, StatCard, make_label
from chest_scanner.logging_setup import LOGS_DIR, logger, now_moscow

# Константы совместимости: прежние имена цветов (используются в коде действий)
BG = PALETTE["BG"]
PANEL = PALETTE["PANEL"]
PANEL_2 = PALETTE["PANEL_2"]
BORDER = PALETTE["BORDER"]
TEXT = PALETTE["TEXT"]
MUTED = PALETTE["MUTED"]
ACCENT = PALETTE["ACCENT"]
ACCENT_HOVER = PALETTE["ACCENT_HOVER"]
GREEN = PALETTE["GREEN"]
RED = PALETTE["RED"]
YELLOW = PALETTE["YELLOW"]


class _GuiBus(QObject):
    """Мост между фоновыми потоками и GUI-потоком."""

    status = Signal(str)          # busy | ready
    refresh_stats = Signal()
    prompt = Signal(str, str)     # unknown_name, image_path
    progress = Signal(int, int)   # done, total (обработка слотов скана)


class ScannerGUI(QMainWindow):
    """Главное окно с боковой навигацией, действиями и журналом."""

    def __init__(self, reader):
        # Важно: QApplication должен существовать ДО создания любых виджетов.
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
        super().__init__()
        self.reader = reader
        app.setStyleSheet(build_stylesheet())
        app.setApplicationName("Chest Scanner")
        app.setWindowIcon(self._app_icon())

        self.setWindowTitle("Chest Scanner")
        self.resize(1120, 780)
        self.setMinimumSize(960, 660)

        self.log_queue = Queue()
        self.busy = False
        self.running = True
        self._settings_dlg = None
        self._hotkey_manager = None
        self._key_timer = QTimer(self)
        self._key_timer.setInterval(80)
        self._key_timer.timeout.connect(self._keyboard_iterate)
        self._log_timer = QTimer(self)
        self._log_timer.setInterval(100)
        self._log_timer.timeout.connect(self._poll_logs)
        self._log_timer.start()

        self._bus = _GuiBus(self)
        self._bus.status.connect(self._apply_status)
        self._bus.refresh_stats.connect(self._refresh_stats)
        self._bus.prompt.connect(self._prompt_gui)
        self._bus.progress.connect(self._apply_progress)

        self._prev_cells_layout = None
        self.reader.unknown_item_prompt = self._prompt_unknown_item

        self._build_ui()
        self._setup_tray()
        self._refresh_profile_label()
        self._refresh_stats()
        self._refresh_hotkeys(initial=True)
        self._show_last_scan(None)
        if self.reader.config.get("auto_scan_enabled", False):
            self.reader.start_auto_scan()
            self._sync_auto_scan_btn()
        profiles = load_profiles()
        if not profiles.get("setup_complete", False):
            QTimer.singleShot(150, self._safe_first_run_wizard)

    # ── Иконка приложения ──
    def _app_icon(self):
        icon_path = os.path.join(paths.BASE_DIR, "Запуск.ico")
        if os.path.isfile(icon_path):
            return QIcon(icon_path)
        return QIcon()

    # ── Построение интерфейса ──
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(14)

        # Заголовок
        header = QHBoxLayout()
        header.setSpacing(14)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(make_label("Chest Scanner", title=True))
        titles.addWidget(make_label("Распознавание сундуков Minecraft", muted=True))
        header.addLayout(titles)
        header.addStretch(1)
        self.profile_label = make_label("Профиль: Default", muted=True, small=True)
        header.addWidget(self.profile_label, 0, Qt.AlignBottom)
        self.status_dot = make_label("●  ГОТОВ", color=GREEN)
        header.addWidget(self.status_dot, 0, Qt.AlignBottom)
        root.addLayout(header)

        # Тело: сайдбар + контент
        body = QHBoxLayout()
        body.setSpacing(14)
        root.addLayout(body, 1)

        tools = [
            ("settings", "⚙  Настройки", self.open_settings),
            ("profiles", "👤  Профили", self.open_profile_manager),
            ("calibrate", "🎯  Калибровка", self._calibrate),
            ("grid_auto", "🎯  Сетка (авто)", self._auto_detect_grid),
            ("templates", "🖼  Шаблоны (авто)", self._auto_templates),
            ("history", "📋  История", self.open_history_dialog),
            ("grid_debug", "🔲  Сетка (отладка)", self._run_grid_debug),
            ("digits_debug", "🔢  Цифры (отладка)", self._digits_debug),
            ("data", "📁  Данные", self._open_data_menu),
            ("stats", "📊  Статистика", self.open_stats_dialog),
            ("log", "📄  Открыть лог", self._open_log_file),
            ("help", "❓  Справка (F1)", self.open_help),
            ("reset", "♻  Сброс настроек", self._reset_config),
        ]
        self._sidebar = SideBar(tools, on_exit=lambda: self.close(force=True))
        body.addWidget(self._sidebar)

        # Контент
        content = QVBoxLayout()
        content.setSpacing(14)
        body.addLayout(content, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        scan_btn = HoverButton("Сканировать", accent=True, min_height=38)
        scan_btn.clicked.connect(lambda: self._run("Сканирование", self._scan, with_progress=True))
        actions.addWidget(scan_btn)
        discord_btn = HoverButton("Discord", min_height=38)
        discord_btn.clicked.connect(lambda: self._run("Отправка в Discord", self._discord))
        actions.addWidget(discord_btn)
        items_btn = HoverButton("Предметы", min_height=38)
        items_btn.clicked.connect(self.open_item_database)
        actions.addWidget(items_btn)
        self.auto_scan_btn = HoverButton("▶ Автоскан", min_height=38)
        self.auto_scan_btn.clicked.connect(self._toggle_auto_scan)
        actions.addWidget(self.auto_scan_btn)
        self.scan_progress = QProgressBar()
        self.scan_progress.setObjectName("scanProgress")
        self.scan_progress.setRange(0, 100)
        self.scan_progress.setValue(0)
        self.scan_progress.setFixedWidth(260)
        self.scan_progress.setVisible(False)
        actions.addWidget(self.scan_progress)
        self.scan_cancel_btn = HoverButton("Отмена", min_height=38)
        self.scan_cancel_btn.setVisible(False)
        self.scan_cancel_btn.clicked.connect(self._cancel_scan)
        actions.addWidget(self.scan_cancel_btn)
        actions.addStretch(1)
        content.addLayout(actions)

        # Карточки статистики
        stats = QHBoxLayout()
        stats.setSpacing(10)
        self.cards = {}
        for key, title, color in [
                ("chests", "Сундуков", None), ("items", "Предметов", None),
                ("unique", "Уникальных", ACCENT_HOVER), ("unknown", "Неизвестных", YELLOW)]:
            card = StatCard(title, "0", color=color)
            stats.addWidget(card)
            self.cards[key] = card
        content.addLayout(stats)

        # Последний скан
        prev = QFrame()
        prev.setProperty("card", True)
        prev_lay = QVBoxLayout(prev)
        prev_lay.setContentsMargins(16, 12, 16, 12)
        prev_lay.setSpacing(4)
        prev_hdr = QHBoxLayout()
        prev_hdr.addWidget(make_label("ПОСЛЕДНИЙ СКАН", section=True))
        prev_hdr.addStretch(1)
        self.last_scan_info = make_label("", muted=True, small=True)
        prev_hdr.addWidget(self.last_scan_info)
        prev_lay.addLayout(prev_hdr)
        self.last_scan_grid = QHBoxLayout()
        self.last_scan_grid.setSpacing(8)
        self.last_scan_grid.addStretch(1)
        prev_lay.addLayout(self.last_scan_grid, 1)
        content.addWidget(prev)

        # Журнал
        log_frame = QFrame()
        log_frame.setProperty("card", True)
        log_lay = QVBoxLayout(log_frame)
        log_lay.setContentsMargins(16, 12, 16, 12)
        log_lay.setSpacing(6)
        log_lay.addWidget(make_label("ЖУРНАЛ", section=True))
        self.log = QPlainTextEdit()
        self.log.setObjectName("logView")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        log_lay.addWidget(self.log, 1)
        content.addWidget(log_frame, 1)

        # Футер
        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.hotkeys_hint_label = make_label(self._hotkeys_hint(), muted=True, small=True)
        footer.addWidget(self.hotkeys_hint_label)
        footer.addStretch(1)
        self.last_scan_label = make_label("Последнее действие: —", muted=True, small=True)
        footer.addWidget(self.last_scan_label)
        content.addLayout(footer)

        self._log("Интерфейс запущен.")
        if KEYBOARD_AVAILABLE:
            self._log(f"Горячие клавиши: {self._hotkeys_hint()}.", GREEN)
        else:
            self._log("Polling-горячие клавиши (keyboard). Рекомендуется Windows-хук.", YELLOW)

    # ── Трей и уведомления ──
    def _setup_tray(self):
        try:
            self._tray = QSystemTrayIcon(self._app_icon(), self)
        except Exception as e:
            logger.error(f"Трей недоступен: {e}")
            self._tray = None
            return
        self._tray.setToolTip("Chest Scanner")
        menu = QMenu(self)
        act_open = menu.addAction("Открыть Chest Scanner")
        act_open.triggered.connect(self._tray_show)
        act_scan = menu.addAction("Сканировать")
        act_scan.triggered.connect(
            lambda: self._safe_after(lambda: self._run("Сканирование", self._scan,
                                                       with_progress=True)))
        self._tray_auto_act = QAction("Автоскан", self)
        self._tray_auto_act.setCheckable(True)
        self._tray_auto_act.setChecked(self.reader.auto_scan_active)
        self._tray_auto_act.triggered.connect(lambda _c: self._toggle_auto_scan())
        menu.addAction(self._tray_auto_act)
        menu.addSeparator()
        act_help = menu.addAction("❓ Справка")
        act_help.triggered.connect(lambda: self._safe_after(self.open_help))
        menu.addSeparator()
        act_exit = menu.addAction("Выход")
        act_exit.triggered.connect(lambda: self.close(force=True))
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        try:
            self._tray.show()
        except Exception as e:
            logger.error(f"Трей: {e}")
            self._tray = None

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick or reason == QSystemTrayIcon.Trigger:
            self._tray_show()

    def _tray_show(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _notify(self, message, title="Chest Scanner"):
        if self._tray is None or not self._tray.isVisible():
            return
        try:
            self._tray.showMessage(title, message,
                                   QSystemTrayIcon.Information, 3000)
        except Exception:
            pass

    # ── Мастер первого запуска ──
    def _safe_first_run_wizard(self):
        try:
            dlg = FirstRunWizard(self.reader, self)
            dlg.exec()
            self._refresh_profile_label()
            self._refresh_stats()
            self._log("✓ Настройка завершена.", GREEN)
        except Exception as e:
            logger.error(f"Мастер первого запуска: {e}", exc_info=True)
            self.showNormal()
            self.raise_()

    # ── Превью последнего скана ──
    def _item_icon_pixmap(self, name):
        try:
            for item in self.reader.items_db.get("items", []):
                if item.get("name") != name:
                    continue
                for p in (item.get("images") or [item.get("image")]):
                    if not p:
                        continue
                    full = absolute_path(p)
                    if full and os.path.isfile(full):
                        pm = QPixmap(full)
                        if not pm.isNull():
                            return pm.scaled(32, 32, Qt.KeepAspectRatio,
                                             Qt.SmoothTransformation)
        except Exception:
            pass
        return None

    def _show_last_scan(self, cd):
        layout = self.last_scan_grid
        while layout.count() > 1:
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        try:
            if cd is None:
                data = load_json(paths.RESULTS_FILE, {"chests": []})
                chests = data.get("chests", []) if isinstance(data, dict) else []
                cd = chests[-1] if chests else None
            if not cd:
                self.last_scan_info.setText("Нет сканирований")
                return
            totals = cd.get("totals", {}) or {}
            ts = cd.get("timestamp", "—")
            self.last_scan_info.setText(
                f"{len(totals)} предм.  •  {cd.get('occupied_slots', 0)} ячеек  •  {ts}")
            top = sorted(totals.items(), key=lambda x: x[1], reverse=True)[:8]
            for name, qty in top:
                cell = QFrame()
                cell.setProperty("card", True)
                v = QVBoxLayout(cell)
                v.setContentsMargins(8, 6, 8, 6)
                v.setSpacing(2)
                pm = self._item_icon_pixmap(name)
                if pm:
                    ic = QLabel()
                    ic.setPixmap(pm)
                    ic.setAlignment(Qt.AlignCenter)
                else:
                    ic = make_label("❓", muted=True)
                v.addWidget(ic)
                nm = make_label(name[:16], small=True)
                nm.setAlignment(Qt.AlignCenter)
                v.addWidget(nm)
                ql = make_label(f"×{qty}", muted=True, small=True)
                ql.setAlignment(Qt.AlignCenter)
                v.addWidget(ql)
                layout.insertWidget(layout.count() - 1, cell)
        except Exception:
            pass

    def _hotkeys_hint(self):
        """Подсказка по горячим клавишам из конфига (пустые значения скрываются)."""
        hk = self.reader.config.get("hotkeys", {})
        parts = []
        for action, label in (("toggle_window", "окно"), ("scan", "скан"),
                              ("discord", "Discord"), ("exit", "выход")):
            key = str(hk.get(action) or "").strip()
            if key:
                parts.append(f"{key.upper()} {label}")
        return "  ".join(parts) if parts else "Горячие клавиши не заданы"

    # ── Инструменты ──
    def _calibrate(self):
        try:
            result = self.reader.calibrate_grid_interactive()
        except Exception as e:
            self._log(f"Ошибка калибровки: {e}", RED)
            logger.error(f"Калибровка: {e}", exc_info=True)
            result = None
        if result:
            self._log(f"✓ Сетка: ({result['x1']},{result['y1']}) -> ({result['x2']},{result['y2']})",
                      GREEN)
        else:
            self._log("Калибровка отменена.", MUTED)

    def _auto_detect_grid(self):
        try:
            result = self.reader.auto_detect_grid()
        except Exception as e:
            self._log(f"Ошибка автоопределения: {e}", RED)
            logger.error(f"Автоопределение сетки: {e}", exc_info=True)
            result = None
        if result:
            self._log(f"✓ Сетка (авто): ({result['x1']},{result['y1']}) -> "
                      f"({result['x2']},{result['y2']}) "
                      f"{result['columns']}x{result['rows']} ячеек", GREEN)
            self._run_grid_debug()
        else:
            self._log("Автоопределение не удалось. Откройте сундук и "
                      "убедитесь, что его интерфейс виден целиком.", YELLOW)

    def _auto_templates(self):
        chest_path = absolute_path(self.reader.config.get("chest_template", ""))
        empty_path = absolute_path(self.reader.config.get("empty_slot_template", ""))
        chest_exists = chest_path and os.path.exists(chest_path)
        empty_exists = empty_path and os.path.exists(empty_path)
        if chest_exists and empty_exists:
            if not self._question("Шаблоны", "Оба шаблона существуют.\nПересоздать?"):
                return
            force = True
        else:
            if not self._question("Шаблоны",
                                  "Создать шаблоны из текущего экрана?\n"
                                  "Убедитесь, что сундук ОТКРЫТ."):
                return
            force = False
        try:
            created = self.reader.auto_generate_templates(force=force)
            if created:
                self._log(f"✓ Созданы шаблоны: {', '.join(created)}", GREEN)
            else:
                self._log("Шаблоны уже существуют.", YELLOW)
        except Exception as e:
            self._log(f"Ошибка автогенерации: {e}", RED)
            logger.error(f"Автогенерация шаблонов: {e}", exc_info=True)

    def _question(self, title, text):
        ret = QMessageBox.question(self, title, text,
                                   QMessageBox.Yes | QMessageBox.No)
        return ret == QMessageBox.Yes

    def _run_grid_debug(self):
        if self.reader._grid_debug_active:
            self._log("Окно отладки уже открыто.", YELLOW)
            return
        threading.Thread(target=self.reader.show_grid_debug, daemon=True).start()

    def _digits_debug(self):
        self._run("Отладка цифр", self.reader.save_quantity_debug)

    def _toggle_auto_scan(self):
        if self.reader.auto_scan_active:
            self.reader.stop_auto_scan()
            self._log("Автосканирование остановлено.", YELLOW)
        else:
            self.reader.start_auto_scan()
            self._log("Автосканирование запущено.", GREEN)
        self._sync_auto_scan_btn()

    def _sync_auto_scan_btn(self):
        active = self.reader.auto_scan_active
        self.auto_scan_btn.setText("⏸ Автоскан" if active else "▶ Автоскан")
        self.auto_scan_btn.setStyleSheet(
            f"background: {GREEN}; color: #04140d; border: 1px solid {GREEN};"
            " font-weight: 600;" if active else "")
        if self._tray is not None:
            self._tray_auto_act.setChecked(active)

    def _open_log_file(self):
        log_path = os.path.join(LOGS_DIR, "scanner.log")
        if not os.path.exists(log_path):
            self._log("Файл лога не найден.", YELLOW)
            return
        try:
            if os.name == "nt":
                os.startfile(log_path)
            else:
                subprocess.Popen(["xdg-open", log_path])
            self._log(f"Лог открыт: {log_path}", GREEN)
        except Exception as e:
            self._log(f"Не удалось открыть лог: {e}", RED)

    def _reset_config(self):
        if not self._question("Сброс",
                              "Сбросить настройки к стандартным?\n"
                              "Текущий конфиг будет сохранён в .backup"):
            return
        try:
            if os.path.exists(paths.CONFIG_FILE):
                shutil.copy2(paths.CONFIG_FILE, paths.CONFIG_FILE + ".backup")
            save_json(paths.CONFIG_FILE, paths.DEFAULT_CONFIG.copy())
            self.reader.reload_profile()
            self._log("✓ Настройки сброшены.", GREEN)
        except Exception as e:
            self._log(f"Ошибка сброса: {e}", RED)

    # ── Основные действия ──
    def _refresh_profile_label(self):
        self.profile_label.setText(f"Профиль: {load_profiles().get('active', 'Default')}")

    def _scan(self):
        self.reader.scan_progress_cb = lambda d, t: self._bus.progress.emit(d, t)
        try:
            result = self.reader.scan_chest(force=True)
        finally:
            self.reader.scan_progress_cb = None
        if self.reader.scan_cancelled:
            self._log("Скан отменён пользователем.", YELLOW)
            self._refresh_stats()
            return
        if result is None:
            reason = getattr(self.reader, "last_scan_reason", None)
            if reason == "no_template":
                self._log("⚠ Шаблон сундука не найден. Создайте его кнопкой "
                          "«Шаблоны (авто)» при открытом сундуке.", YELLOW)
            elif reason == "chest_closed":
                self._log("⚠ Сундук не открыт. Откройте сундук в игре "
                          "и повторите сканирование.", YELLOW)
            else:
                self._log("Сундук не найден или нет изменений.", MUTED)
            self._refresh_stats()
            return
        self._show_last_scan(result)
        totals = result.get("totals", {}) or {}
        unknowns = set(result.get("unknown_items", []) or [])
        unknown_left = sorted(name for name in unknowns if name.startswith("unknown_"))
        if self._tray is not None and self.reader.config.get("notify_on_scan", True):
            msg = (f"Сканировано: {result.get('occupied_slots', 0)} ячеек, "
                   f"{result.get('unique_items', 0)} уникальных.")
            if unknown_left:
                msg += f"\n⚠ Неизвестных предметов: {len(unknown_left)}"
            self._notify(msg, "Chest Scanner")
        self._log("✓ Сундук отсканирован.", GREEN)
        self._log("┌─ Содержимое", ACCENT)
        self._log(f"│ Время: {result.get('timestamp', '—')}", MUTED)
        self._log(f"│ Ячеек: {result.get('occupied_slots', 0)}", MUTED)
        if not totals:
            self._log("│ Пуст.", MUTED)
        for name, qty in sorted(totals.items(), key=lambda x: x[0].lower()):
            sfx = "  [неизвестный]" if name in unknowns else ""
            self._log(f"│ {name}: {qty}{sfx}")
        if unknown_left:
            self._log(f"│ Неизвестных предметов: {len(unknown_left)}", YELLOW)
        self._log("└─ Конец", ACCENT)
        self._refresh_stats()

    def _discord(self):
        ok, msg = self.reader.send_all_chests_to_discord()
        if ok:
            self._log(f"✓ {msg}", GREEN)
        else:
            self._log(f"✗ {msg}", RED)
            QMessageBox.critical(self, "Discord", msg)
        self._refresh_stats()

    def _run(self, title, func, with_progress=False):
        if self.busy:
            self._log("Подождите: предыдущая операция выполняется.", YELLOW)
            return

        def worker():
            self.busy = True
            self._bus.status.emit("busy")
            self._log(f"▶ {title}...")
            try:
                if with_progress:
                    self._safe_after(self._show_scan_progress)
                func()
                if with_progress:
                    self._safe_after(self._finish_scan_progress)
                self._log(f"✓ {title}: готово.", GREEN)
            except Exception as e:
                self._log(f"✕ {title}: {type(e).__name__}: {e}", RED)
                logger.error(f"{title}: {e}", exc_info=True)
            finally:
                self.busy = False
                self._bus.status.emit("ready")
                self._bus.refresh_stats.emit()
                if with_progress:
                    self._safe_after(self._hide_scan_progress)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_status(self, state):
        if state == "busy":
            self.status_dot.setText("●  РАБОТАЕТ")
            self.status_dot.setStyleSheet(f"color: {YELLOW};")
        else:
            self.status_dot.setText("●  ГОТОВ")
            self.status_dot.setStyleSheet(f"color: {GREEN};")

    # ── Прогресс и отмена сканирования ──
    def _apply_progress(self, done, total):
        if total <= 0:
            self.scan_progress.setRange(0, 100)
            self.scan_progress.setValue(0)
            return
        self.scan_progress.setRange(0, total)
        self.scan_progress.setValue(done)

    def _show_scan_progress(self):
        self.scan_cancel_btn.setEnabled(True)
        self.scan_progress.setValue(0)
        self.scan_progress.setVisible(True)
        self.scan_cancel_btn.setVisible(True)

    def _finish_scan_progress(self):
        self.scan_progress.setValue(self.scan_progress.maximum())

    def _hide_scan_progress(self):
        self.scan_progress.setVisible(False)
        self.scan_cancel_btn.setVisible(False)

    def _cancel_scan(self):
        self.reader.request_scan_cancel()
        self.scan_cancel_btn.setEnabled(False)
        self._log("Отмена сканирования...", YELLOW)

    # ── Горячие клавиши ──
    def _start_win_hotkeys(self):
        """Регистрирует горячие клавиши через RegisterHotKey (без UAC)."""
        try:
            from chest_scanner.hotkeys import WinHotkeyManager
        except Exception:
            return None
        hk = self.reader.config.get("hotkeys", {})
        actions = {"toggle_window": self._toggle_window,
                   "scan": lambda: self._run("Сканирование", self._scan, with_progress=True),
                   "discord": lambda: self._run("Discord", self._discord),
                   "exit": lambda: self.close(force=True)}
        mgr = WinHotkeyManager()
        if not mgr.start():
            return None
        registered = 0
        for action, method in actions.items():
            key = str(hk.get(action) or "").strip().lower()
            if not key:
                continue
            if mgr.register(key, lambda m=method: self._safe_after(m)):
                registered += 1
        if registered == 0:
            try:
                mgr.stop()
            except Exception:
                pass
            return None
        self._log(f"Windows-хук горячих клавиш активен ({registered}).", GREEN)
        return mgr

    def _safe_after(self, func):
        QTimer.singleShot(0, func)

    def _refresh_hotkeys(self, initial=False):
        """Пересоздаёт горячие клавиши; при неудаче WinAPI — polling."""
        if self._hotkey_manager:
            try:
                self._hotkey_manager.stop()
            except Exception:
                pass
            self._hotkey_manager = None
        mgr = self._start_win_hotkeys()
        if mgr is not None:
            self._hotkey_manager = mgr
            self._key_timer.stop()
            return
        if initial:
            self._key_timer.start()

    def _keyboard_iterate(self):
        if not self.running or not KEYBOARD_AVAILABLE:
            return
        try:
            hk = self.reader.config.get("hotkeys", {})
            k_toggle = str(hk.get("toggle_window") or "").strip().lower()
            k_scan = str(hk.get("scan") or "").strip().lower()
            k_discord = str(hk.get("discord") or "").strip().lower()
            k_exit = str(hk.get("exit") or "").strip().lower()
            if k_toggle and keyboard.is_pressed(k_toggle):
                self._toggle_window()
                time.sleep(0.4)
            elif k_scan and keyboard.is_pressed(k_scan):
                self._run("Сканирование", self._scan, with_progress=True)
                time.sleep(0.4)
            elif k_discord and keyboard.is_pressed(k_discord):
                self._run("Discord", self._discord)
                time.sleep(0.4)
            elif k_exit and keyboard.is_pressed(k_exit):
                self.close(force=True)
                return
        except Exception as e:
            self._log(f"Ошибка горячей клавиши: {e}", RED)
            logger.error(f"Hotkey: {e}", exc_info=True)

    def _toggle_window(self):
        """Показывает или скрывает главное окно (горячая клавиша)."""
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self._tray_show()

    # ── Статистика и журнал ──
    def _refresh_stats(self):
        try:
            data = load_json(paths.RESULTS_FILE, {"chests": []})
            chests = data.get("chests", []) if isinstance(data, dict) else []
            totals, unknown = {}, set()
            for ch in chests:
                for n, q in (ch.get("totals", {}) or {}).items():
                    totals[n] = totals.get(n, 0) + int(q or 0)
                unknown.update(ch.get("unknown_items", []) or [])
            self.cards["chests"].set_value(len(chests))
            self.cards["items"].set_value(f"{sum(totals.values()):,}".replace(",", " "))
            self.cards["unique"].set_value(len(totals))
            self.cards["unknown"].set_value(len(unknown))
        except Exception:
            pass

    def _log(self, message, color=None):
        self.log_queue.put((f"[{now_moscow().strftime('%H:%M:%S')}] {message}", color))

    def _poll_logs(self):
        try:
            while True:
                message, color = self.log_queue.get_nowait()
                escaped = html.escape(message)
                if color:
                    self.log.appendHtml(f'<span style="color:{color}">{escaped}</span>')
                else:
                    self.log.appendHtml(f"<span>{escaped}</span>")
                try:
                    action = message.split("] ", 1)[1]
                except IndexError:
                    action = message
                self.last_scan_label.setText(f"Последнее: {action}")
        except Empty:
            pass

    # ── Неизвестный предмет ──
    def _prompt_unknown_item(self, unknown_name, image_path):
        """Вызывается из потока сканирования; ждёт ответа пользователя."""
        result = {"name": ""}
        done = threading.Event()
        self._prompt_state = (done, result)
        try:
            self._bus.prompt.emit(unknown_name, image_path)
        except Exception:
            done.set()
            return ""
        done.wait()
        return result["name"]

    def _prompt_gui(self, unknown_name, image_path):
        dlg = UnknownItemDialog(unknown_name, image_path, self)
        done, result = getattr(self, "_prompt_state", (None, None))
        if dlg.exec() == UnknownItemDialog.Accepted:
            self.reader.unknown_item_category = dlg.category_key
            if result is not None:
                result["name"] = dlg.item_name()
        elif result is not None:
            result["name"] = ""
        if done is not None:
            done.set()

    # ── Открытие диалогов ──
    def open_settings(self):
        if self._settings_dlg is not None and self._settings_dlg.isVisible():
            self._settings_dlg.raise_()
            return
        self._settings_dlg = SettingsDialog(self, self)
        self._settings_dlg.exec()

    def open_profile_manager(self):
        if self.busy:
            self._log("Нельзя менять профиль во время операции.", YELLOW)
            return
        ProfileManagerDialog(self, self).exec()

    def open_history_dialog(self):
        HistoryDialog(self).exec()

    def open_export_dialog(self):
        ExportDialog(self).exec()

    def _open_data_menu(self):
        """Единое меню «Данные»: экспорт и импорт в одном месте."""
        menu = QMenu(self)
        act_export = menu.addAction("📤  Экспорт данных (CSV/JSON)")
        act_export.triggered.connect(self.open_export_dialog)
        act_export_db = menu.addAction("🗂  Экспорт базы предметов (ZIP)")
        act_export_db.triggered.connect(self._export_item_db)
        act_import_db = menu.addAction("📥  Импорт базы предметов (ZIP)")
        act_import_db.triggered.connect(self._import_item_db)
        btn = self._sidebar.buttons["data"]
        menu.exec_(btn.mapToGlobal(QPoint(0, btn.height())))

    def open_item_database(self):
        ItemDatabaseDialog(self.reader, self).exec()

    def open_stats_dialog(self):
        StatsDialog(self).exec()

    def open_help(self):
        HelpDialog(self).exec()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F1:
            self.open_help()
            return
        super().keyPressEvent(event)

    def _export_item_db(self):
        ts = time.strftime("%Y%m%d_%H%M%S")
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт базы предметов", f"items_db_{ts}.zip",
            "Архив базы (*.zip)")
        if not path:
            return
        try:
            from chest_scanner.item_pack import export_item_db
            export_item_db(self.reader.items_db, path)
            self._log(f"✓ База экспортирована: {os.path.basename(path)}", GREEN)
            QMessageBox.information(self, "Экспорт базы", f"База сохранена:\n{path}")
        except Exception as e:
            self._log(f"Ошибка экспорта базы: {e}", RED)
            QMessageBox.critical(self, "Экспорт базы", str(e))

    def _import_item_db(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Импорт базы предметов", "", "Архив базы (*.zip);;Все файлы (*.*)")
        if not path:
            return
        merge = self._question(
            "Импорт базы",
            "Объединить с текущей базой?\n"
            "(Да — предметы из файла добавятся или обновятся,\n"
            " Нет — база будет заменена полностью).")
        try:
            from chest_scanner.item_pack import import_item_db
            merged = import_item_db(path, self.reader.items_db,
                                    mode="merge" if merge else "replace")
            self.reader.items_db = merged
            save_json(paths.ITEMS_FILE, merged)
            self.reader.template_cache.invalidate()
            self.reader._rebuild_indexes()
            self._refresh_stats()
            self._log(f"✓ База импортирована: {len(merged.get('items', []))} предметов",
                      GREEN)
        except Exception as e:
            self._log(f"Ошибка импорта базы: {e}", RED)
            QMessageBox.critical(self, "Импорт базы", str(e))

    # ── Закрытие ──
    def closeEvent(self, event: QCloseEvent):
        if not self.running:
            event.accept()
            return
        if self._tray is not None and self.reader.config.get("tray_minimize_to_tray", False):
            self.hide()
            self._notify("Приложение работает в фоне. Меню — иконка в трее.",
                         "Chest Scanner")
            event.ignore()
            return
        self.close(force=True)
        event.accept()

    def close(self, force=False):
        if not self.running:
            return
        self.running = False
        self._key_timer.stop()
        self._log_timer.stop()
        try:
            if self._hotkey_manager:
                self._hotkey_manager.stop()
                self._hotkey_manager = None
        except Exception:
            pass
        try:
            if self._tray is not None:
                self._tray.hide()
        except Exception:
            pass
        try:
            if self.reader.auto_scan_active:
                self.reader.stop_auto_scan()
        except Exception:
            pass
        QApplication.instance().quit()

    def run(self):
        self.show()
        return QApplication.instance().exec()