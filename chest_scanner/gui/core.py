"""GUI mixin: главное окно, цикл обработки, действия и инструменты."""
import os
import shutil
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk
from queue import Empty, Queue

from chest_scanner.app_bootstrap import KEYBOARD_AVAILABLE, keyboard
from chest_scanner.commons import absolute_path, load_json, save_json
from chest_scanner.config import load_profiles
from chest_scanner.logging_setup import LOGS_DIR, logger, now_moscow
from chest_scanner import paths


class GuiCoreMixin:
    BG = "#101318"
    PANEL = "#171b22"
    PANEL_2 = "#1d232c"
    BORDER = "#2a313c"
    TEXT = "#f2f4f7"
    MUTED = "#8f9aaa"
    ACCENT = "#5865f2"
    ACCENT_HOVER = "#6975ff"
    GREEN = "#35c98b"
    RED = "#f25f67"
    YELLOW = "#f0b84b"

    def __init__(self, reader):
        self.reader = reader
        self.root = tk.Tk()
        self.root.title("Chest Scanner")
        self.root.geometry("1120x780")
        self.root.minsize(960, 660)
        self.root.configure(bg=self.BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.log_queue = Queue()
        self.busy = False
        self.running = True
        self.reader.unknown_item_prompt = self._prompt_unknown_item
        self._settings_win = None
        self._setup_style()
        self._build_ui()
        self._refresh_profile_label()
        self._refresh_stats()
        self._poll_logs()
        self._keyboard_loop()
        if self.reader.config.get("auto_scan_enabled", False):
            self.reader.start_auto_scan()
            self.auto_scan_btn.configure(text="⏸ Автоскан", bg=self.GREEN)
        profiles = load_profiles()
        if not profiles.get("setup_complete", False):
            self.root.after(150, self._safe_first_run_wizard)

    def _setup_style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background=self.PANEL, fieldbackground=self.PANEL,
                        foreground=self.TEXT, rowheight=40, borderwidth=0, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", background=self.PANEL_2, foreground=self.MUTED,
                        relief="flat", font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#29304a")])
        style.configure("TCombobox", fieldbackground=self.PANEL_2, background=self.PANEL_2,
                        foreground=self.TEXT, arrowcolor=self.TEXT, borderwidth=0)

    def _release_all_grabs(self):
        released = []
        for w in [self.root] + list(self.root.winfo_children()):
            try:
                if w.grab_current() is not None:
                    w.grab_release()
                    released.append(w)
            except Exception:
                pass
        return released

    def _restore_grabs(self, windows):
        for w in windows:
            try:
                if w.winfo_exists():
                    w.grab_set()
            except Exception:
                pass

    def _modal(self, title, geometry="620x420", minsize=None):
        d = tk.Toplevel(self.root)
        d.title(title)
        d.geometry(geometry)
        if minsize:
            d.minsize(*minsize)
        d.configure(bg=self.BG)
        try:
            if self.root.winfo_exists() and self.root.winfo_viewable():
                d.transient(self.root)
            d.update_idletasks()
            d.deiconify()
            d.lift()
            d.focus_force()
            d.grab_set()
        except tk.TclError:
            pass
        return d

    def _build_ui(self):
        header = tk.Frame(self.root, bg=self.BG)
        header.pack(fill="x", padx=24, pady=(20, 12))
        left = tk.Frame(header, bg=self.BG)
        left.pack(side="left")
        tk.Label(left, text="Chest Scanner", bg=self.BG, fg=self.TEXT,
                 font=("Segoe UI", 24, "bold")).pack(anchor="w")
        tk.Label(left, text="Распознавание сундуков Minecraft", bg=self.BG, fg=self.MUTED,
                 font=("Segoe UI", 10)).pack(anchor="w", pady=(2, 0))
        self.profile_label = tk.Label(header, text="Профиль: Default", bg=self.BG,
                                      fg=self.MUTED, font=("Segoe UI", 9, "bold"))
        self.profile_label.pack(side="right", pady=10, padx=(0, 14))
        self.status_dot = tk.Label(header, text="●  ГОТОВ", bg=self.BG, fg=self.GREEN,
                                   font=("Segoe UI", 10, "bold"))
        self.status_dot.pack(side="right", pady=10)

        body = tk.Frame(self.root, bg=self.BG)
        body.pack(fill="both", expand=True, padx=24, pady=(0, 14))

        sidebar = tk.Frame(body, bg=self.PANEL, width=195,
                           highlightbackground=self.BORDER, highlightthickness=1)
        sidebar.pack(side="left", fill="y", padx=(0, 14))
        sidebar.pack_propagate(False)
        tk.Label(sidebar, text="ИНСТРУМЕНТЫ", bg=self.PANEL, fg=self.MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=14, pady=(14, 10))

        def sb(text, cmd, icon=""):
            fr = tk.Frame(sidebar, bg=self.PANEL)
            fr.pack(fill="x", padx=8, pady=2)
            lbl = f"{icon}  {text}" if icon else text
            btn = tk.Button(fr, text=lbl, command=cmd, bg=self.PANEL, fg=self.TEXT,
                            activebackground=self.PANEL_2, activeforeground=self.TEXT,
                            relief="flat", bd=0, anchor="w", padx=10, pady=9,
                            cursor="hand2", font=("Segoe UI", 10))
            btn.pack(fill="x")
            btn.bind("<Enter>", lambda e: btn.configure(bg=self.PANEL_2))
            btn.bind("<Leave>", lambda e: btn.configure(bg=self.PANEL))

        sb("Настройки", self.open_settings, "⚙")
        sb("Профили", self.open_profile_manager, "👤")
        sb("Калибровка", self._calibrate, "🎯")
        sb("Шаблоны (авто)", self._auto_templates, "🖼")
        sb("История", self.open_history_dialog, "📋")
        sb("Сетка (отладка)", self._run_grid_debug, "🔲")
        sb("Цифры (отладка)", self._digits_debug, "🔢")
        sb("Экспорт", self.open_export_dialog, "📤")
        sb("Открыть лог", self._open_log_file, "📄")
        sb("Сброс настроек", self._reset_config, "♻")

        tk.Frame(sidebar, bg=self.BORDER, height=1).pack(fill="x", padx=12, pady=12)
        sb("Выход", self.close, "✕")

        main_area = tk.Frame(body, bg=self.BG)
        main_area.pack(side="left", fill="both", expand=True)

        actions = tk.Frame(main_area, bg=self.BG)
        actions.pack(fill="x", pady=(0, 14))
        self._button(actions, "Сканировать",
                     lambda: self._run("Сканирование", self._scan), True).pack(side="left", padx=(0, 6))
        self._button(actions, "Discord",
                     lambda: self._run("Отправка в Discord", self._discord)).pack(side="left", padx=3)
        self._button(actions, "Предметы", self.open_item_database).pack(side="left", padx=3)
        self.auto_scan_btn = self._button(actions, "▶ Автоскан", self._toggle_auto_scan)
        self.auto_scan_btn.pack(side="left", padx=3)

        stats = tk.Frame(main_area, bg=self.BG)
        stats.pack(fill="x", pady=(0, 14))
        for i in range(4):
            stats.grid_columnconfigure(i, weight=1)
        self.cards = {}
        for i, (key, title, color) in enumerate([
            ("chests", "Сундуков", None), ("items", "Предметов", None),
            ("unique", "Уникальных", self.ACCENT_HOVER), ("unknown", "Неизвестных", self.YELLOW)]):
            card, label = self._card(stats, title, "0", color)
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 6, 6 if i < 3 else 0))
            self.cards[key] = label

        lp = tk.Frame(main_area, bg=self.PANEL, highlightbackground=self.BORDER, highlightthickness=1)
        lp.pack(fill="both", expand=True)
        tk.Label(lp, text="ЖУРНАЛ", bg=self.PANEL, fg=self.MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=16, pady=(14, 8))
        self.log = tk.Text(lp, bg=self.PANEL, fg="#cbd2dc", insertbackground=self.TEXT,
                           relief="flat", bd=0, wrap="word", font=("Consolas", 9), padx=16, pady=4)
        self.log.pack(fill="both", expand=True, side="left")
        scr = tk.Scrollbar(lp, command=self.log.yview)
        scr.pack(side="right", fill="y", padx=(0, 7), pady=(0, 8))
        self.log.configure(yscrollcommand=scr.set)

        footer = tk.Frame(main_area, bg=self.BG)
        footer.pack(fill="x", pady=(10, 0))
        self.hotkeys_hint_label = tk.Label(footer, text=self._hotkeys_hint(),
                                           bg=self.BG, fg=self.MUTED, font=("Segoe UI", 9))
        self.hotkeys_hint_label.pack(side="left")
        self.last_scan_label = tk.Label(footer, text="Последнее действие: —",
                                        bg=self.BG, fg=self.MUTED, font=("Segoe UI", 9))
        self.last_scan_label.pack(side="right")

        self._log("Интерфейс запущен.")
        if KEYBOARD_AVAILABLE:
            self._log(f"Горячие клавиши: {self._hotkeys_hint()}.", self.GREEN)
        else:
            self._log("Горячие клавиши недоступны (нужны права администратора).", self.YELLOW)

    def _hotkeys_hint(self):
        """Подсказка по горячим клавишам из конфига (пустые значения скрываются)."""
        hk = self.reader.config.get("hotkeys", {})
        parts = []
        for action, label in (("digits", "цифры"), ("grid", "сетка"), ("scan", "скан"),
                              ("discord", "Discord"), ("exit", "выход")):
            key = str(hk.get(action) or "").strip()
            if key:
                parts.append(f"{key.upper()} {label}")
        return "  ".join(parts) if parts else "Горячие клавиши не заданы"

    # ── Инструменты ──
    def _calibrate(self):
        saved = self._release_all_grabs()
        try:
            result = self.reader.calibrate_grid_interactive()
        except Exception as e:
            self._log(f"Ошибка калибровки: {e}", self.RED)
            result = None
        finally:
            self._restore_grabs(saved)
        if result:
            self._log(f"✓ Сетка: ({result['x1']},{result['y1']}) -> ({result['x2']},{result['y2']})", self.GREEN)
        else:
            self._log("Калибровка отменена.", self.MUTED)

    def _auto_templates(self):
        from tkinter import messagebox
        chest_path = absolute_path(self.reader.config.get("chest_template", ""))
        empty_path = absolute_path(self.reader.config.get("empty_slot_template", ""))
        chest_exists = chest_path and os.path.exists(chest_path)
        empty_exists = empty_path and os.path.exists(empty_path)
        if chest_exists and empty_exists:
            if not messagebox.askyesno("Шаблоны", "Оба шаблона существуют.\nПересоздать?", parent=self.root):
                return
            force = True
        else:
            if not messagebox.askyesno("Шаблоны",
                                       "Создать шаблоны из текущего экрана?\n"
                                       "Убедитесь, что сундук ОТКРЫТ.", parent=self.root):
                return
            force = False
        try:
            created = self.reader.auto_generate_templates(force=force)
            if created:
                self._log(f"✓ Созданы шаблоны: {', '.join(created)}", self.GREEN)
            else:
                self._log("Шаблоны уже существуют.", self.YELLOW)
        except Exception as e:
            self._log(f"Ошибка автогенерации: {e}", self.RED)
            logger.error(f"Автогенерация шаблонов: {e}", exc_info=True)

    def _run_grid_debug(self):
        if self.reader._grid_debug_active:
            self._log("Окно отладки уже открыто.", self.YELLOW)
            return
        saved = self._release_all_grabs()

        def work():
            try:
                self.reader.show_grid_debug()
            except Exception as e:
                logger.error(f"Grid debug: {e}", exc_info=True)
            finally:
                try:
                    self.root.after(0, lambda: self._restore_grabs(saved))
                except Exception:
                    pass

        threading.Thread(target=work, daemon=True).start()

    def _digits_debug(self):
        self._run("Отладка цифр", self.reader.save_quantity_debug)

    def _toggle_auto_scan(self):
        if self.reader.auto_scan_active:
            self.reader.stop_auto_scan()
            self.auto_scan_btn.configure(text="▶ Автоскан", bg=self.PANEL_2)
            self._log("Автосканирование остановлено.", self.YELLOW)
        else:
            self.reader.start_auto_scan()
            self.auto_scan_btn.configure(text="⏸ Автоскан", bg=self.GREEN)
            self._log("Автосканирование запущено.", self.GREEN)

    def _open_log_file(self):
        log_path = os.path.join(LOGS_DIR, "scanner.log")
        if not os.path.exists(log_path):
            self._log("Файл лога не найден.", self.YELLOW)
            return
        try:
            if os.name == "nt":
                os.startfile(log_path)
            else:
                subprocess.Popen(["xdg-open", log_path])
            self._log(f"Лог открыт: {log_path}", self.GREEN)
        except Exception as e:
            self._log(f"Не удалось открыть лог: {e}", self.RED)

    def _reset_config(self):
        from tkinter import messagebox
        if not messagebox.askyesno("Сброс", "Сбросить настройки к стандартным?\n"
                                             "Текущий конфиг будет сохранён в .backup", parent=self.root):
            return
        try:
            if os.path.exists(paths.CONFIG_FILE):
                shutil.copy2(paths.CONFIG_FILE, paths.CONFIG_FILE + ".backup")
            save_json(paths.CONFIG_FILE, paths.DEFAULT_CONFIG.copy())
            self.reader.reload_profile()
            self._log("✓ Настройки сброшены.", self.GREEN)
        except Exception as e:
            self._log(f"Ошибка сброса: {e}", self.RED)

    # ── Основные действия ──
    def _refresh_profile_label(self):
        self.profile_label.configure(text=f"Профиль: {load_profiles().get('active', 'Default')}")

    def _scan(self):
        result = self.reader.scan_chest(force=True)
        if result is None:
            self._log("Сундук не найден или нет изменений.", self.MUTED)
            self._refresh_stats()
            return
        self._log("✓ Сундук отсканирован.", self.GREEN)
        self._log("┌─ Содержимое", self.ACCENT)
        self._log(f"│ Время: {result.get('timestamp', '—')}", self.MUTED)
        self._log(f"│ Ячеек: {result.get('occupied_slots', 0)}", self.MUTED)
        totals = result.get("totals", {}) or {}
        unknowns = set(result.get("unknown_items", []) or [])
        if not totals:
            self._log("│ Пуст.", self.MUTED)
        for name, qty in sorted(totals.items(), key=lambda x: x[0].lower()):
            sfx = "  [неизвестный]" if name in unknowns else ""
            self._log(f"│ {name}: {qty}{sfx}")
        self._log("└─ Конец", self.ACCENT)
        self._refresh_stats()

    def _discord(self):
        from tkinter import messagebox
        ok, msg = self.reader.send_all_chests_to_discord()
        if ok:
            self._log(f"✓ {msg}", self.GREEN)
        else:
            self._log(f"✗ {msg}", self.RED)
            messagebox.showerror("Discord", msg)
        self._refresh_stats()

    def _run(self, title, func):
        if self.busy:
            self._log("Подождите: предыдущая операция выполняется.", self.YELLOW)
            return

        def worker():
            self.busy = True
            try:
                self.root.after(0, lambda: self.status_dot.configure(text="●  РАБОТАЕТ", fg=self.YELLOW))
            except Exception:
                pass
            self._log(f"▶ {title}...")
            try:
                func()
                self._log(f"✓ {title}: готово.", self.GREEN)
            except Exception as e:
                self._log(f"✕ {title}: {type(e).__name__}: {e}", self.RED)
                logger.error(f"{title}: {e}", exc_info=True)
            finally:
                self.busy = False
                try:
                    self.root.after(0, lambda: self.status_dot.configure(text="●  ГОТОВ", fg=self.GREEN))
                    self.root.after(0, self._refresh_stats)
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()

    def _keyboard_loop(self):
        if not self.running:
            return
        if KEYBOARD_AVAILABLE:
            try:
                hk = self.reader.config.get("hotkeys", {})
                k_digits = str(hk.get("digits") or "").strip().lower()
                k_grid = str(hk.get("grid") or "").strip().lower()
                k_scan = str(hk.get("scan") or "").strip().lower()
                k_discord = str(hk.get("discord") or "").strip().lower()
                k_exit = str(hk.get("exit") or "").strip().lower()
                if k_digits and keyboard.is_pressed(k_digits):
                    self._digits_debug()
                    time.sleep(0.4)
                elif k_grid and keyboard.is_pressed(k_grid):
                    self._run_grid_debug()
                    time.sleep(0.4)
                elif k_scan and keyboard.is_pressed(k_scan):
                    self._run("Сканирование", self._scan)
                    time.sleep(0.4)
                elif k_discord and keyboard.is_pressed(k_discord):
                    self._run("Discord", self._discord)
                    time.sleep(0.4)
                elif k_exit and keyboard.is_pressed(k_exit):
                    self.close()
                    return
            except Exception as e:
                self._log(f"Ошибка горячей клавиши: {e}", self.RED)
                logger.error(f"Hotkey: {e}", exc_info=True)
        try:
            self.root.after(80, self._keyboard_loop)
        except Exception:
            pass

    def _refresh_stats(self):
        try:
            data = load_json(paths.RESULTS_FILE, {"chests": []})
            chests = data.get("chests", []) if isinstance(data, dict) else []
            totals, unknown = {}, set()
            for ch in chests:
                for n, q in (ch.get("totals", {}) or {}).items():
                    totals[n] = totals.get(n, 0) + int(q or 0)
                unknown.update(ch.get("unknown_items", []) or [])
            self.cards["chests"].configure(text=str(len(chests)))
            self.cards["items"].configure(text=f"{sum(totals.values()):,}".replace(",", " "))
            self.cards["unique"].configure(text=str(len(totals)))
            self.cards["unknown"].configure(text=str(len(unknown)))
        except Exception:
            pass

    def _log(self, message, color=None):
        stamp = now_moscow().strftime("%H:%M:%S")
        self.log_queue.put((f"[{stamp}] {message}", color))

    def _poll_logs(self):
        try:
            while True:
                message, color = self.log_queue.get_nowait()
                self.log.insert("end", message + "\n")
                if color:
                    tag = f"color_{abs(hash(color))}"
                    self.log.tag_configure(tag, foreground=color)
                    self.log.tag_add(tag, "end-2l linestart", "end-1l lineend")
                self.log.see("end")
                try:
                    action = message.split("] ", 1)[1]
                except IndexError:
                    action = message
                self.last_scan_label.configure(text=f"Последнее: {action}")
        except Empty:
            pass
        if self.running:
            try:
                self.root.after(100, self._poll_logs)
            except Exception:
                pass

    def close(self):
        if not self.running:
            return
        self.running = False
        try:
            if self.reader.auto_scan_active:
                self.reader.stop_auto_scan()
        except Exception:
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self):
        self.root.mainloop()
