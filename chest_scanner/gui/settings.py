"""GUI mixin: окно настроек с вкладками и сохранением."""
import os
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import requests

from chest_scanner.commons import save_json
from chest_scanner import paths


class GuiSettingsMixin:
    def open_settings(self):
        if self._settings_win is not None:
            try:
                if self._settings_win.winfo_exists():
                    self._settings_win.lift()
                    self._settings_win.focus_force()
                    return
            except tk.TclError:
                pass

        win = tk.Toplevel(self.root)
        self._settings_win = win
        win.title("Настройки")
        win.geometry("1060x700")
        win.minsize(940, 620)
        win.configure(bg=self.BG)
        try:
            win.transient(self.root)
            win.grab_set()
            win.lift()
            win.focus_force()
        except tk.TclError:
            pass

        tk.Label(win, text="Настройки", bg=self.BG, fg=self.TEXT,
                 font=("Segoe UI", 22, "bold")).pack(anchor="w", padx=22, pady=(18, 4))

        bottom = tk.Frame(win, bg=self.BG)
        bottom.pack(fill="x", padx=22, pady=(10, 14))

        def close_settings():
            try:
                win.grab_release()
            except tk.TclError:
                pass
            win.destroy()

        def save_action():
            self._save_settings_from_gui()

        tk.Label(bottom, text="Изменения применяются после «Сохранить».",
                 bg=self.BG, fg=self.MUTED, font=("Segoe UI", 9)).pack(side="left")
        self._button(bottom, "Сохранить", save_action, True).pack(side="right")
        self._button(bottom, "Закрыть", close_settings).pack(side="right", padx=8)

        body = tk.Frame(win, bg=self.BG)
        body.pack(fill="both", expand=True, padx=22, pady=(0, 18))

        nav = tk.Frame(body, bg=self.PANEL, width=220,
                       highlightbackground=self.BORDER, highlightthickness=1)
        nav.pack(side="left", fill="y", padx=(0, 14))
        nav.pack_propagate(False)
        tk.Label(nav, text="РАЗДЕЛЫ", bg=self.PANEL, fg=self.MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=14, pady=(14, 8))

        content = tk.Frame(body, bg=self.BG)
        content.pack(side="left", fill="both", expand=True)

        self._settings_nav = {}
        self._settings_pages = {}
        self._settings_active = "general"

        nav_items = [
            ("general", "⚙  Основные"),
            ("detection", "🎯  Распознавание"),
            ("grid", "🧱  Сетка"),
            ("templates", "🖼  Шаблоны"),
            ("discord", "📨  Discord"),
        ]

        for key, label in nav_items:
            btn = tk.Button(nav, text=label, command=lambda k=key: self._show_settings_page(k),
                            bg=self.PANEL, fg=self.TEXT, activebackground=self.PANEL_2,
                            activeforeground=self.TEXT, relief="flat", bd=0, anchor="w",
                            padx=14, pady=10, cursor="hand2", font=("Segoe UI", 10, "bold"))
            btn.pack(fill="x", padx=8, pady=2)
            self._settings_nav[key] = btn

        self._init_settings_vars()

        for key, _ in nav_items:
            outer, inner = self._settings_page(content)
            self._settings_pages[key] = outer
            getattr(self, f"_settings_page_{key}")(inner)

        self._show_settings_page("general")
        win.protocol("WM_DELETE_WINDOW", close_settings)

    def _settings_page(self, parent):
        outer = tk.Frame(parent, bg=self.BG)
        canvas = tk.Canvas(outer, bg=self.BG, highlightthickness=0, bd=0)
        scrollbar = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=self.BG)
        wid = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(wid, width=e.width))

        def on_wheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", on_wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        return outer, inner

    def _show_settings_page(self, key):
        self._settings_active = key
        for pk, page in self._settings_pages.items():
            if pk == key:
                page.pack(fill="both", expand=True)
            else:
                page.forget()
        for bk, btn in self._settings_nav.items():
            if bk == key:
                btn.configure(bg=self.ACCENT, fg="white")
            else:
                btn.configure(bg=self.PANEL, fg=self.TEXT)

    def _init_settings_vars(self):
        cfg = self.reader.config
        grid = cfg.get("chest_grid", paths.DEFAULT_CONFIG["chest_grid"])
        dm = cfg.get("discord_message", {})
        v = {}
        ref = cfg.get("reference_screen_size", [1920, 1080])
        v["ref_w"] = tk.StringVar(value=str(ref[0] if isinstance(ref, list) and len(ref) > 0 else 1920))
        v["ref_h"] = tk.StringVar(value=str(ref[1] if isinstance(ref, list) and len(ref) > 1 else 1080))
        v["scan_interval"] = tk.DoubleVar(value=max(0.5, min(10.0, float(cfg.get("scan_interval_seconds", 2)))))
        v["print_candidates"] = tk.BooleanVar(value=bool(cfg.get("print_item_candidates", False)))
        v["auto_scan"] = tk.BooleanVar(value=bool(cfg.get("auto_scan_enabled", False)))
        v["chest_threshold"] = tk.DoubleVar(value=max(0.5, min(0.99, float(cfg.get("chest_template_threshold", 0.82)))))
        v["empty_threshold"] = tk.DoubleVar(value=max(100, min(5000, float(cfg.get("empty_slot_threshold", 600)))))
        v["item_threshold"] = tk.DoubleVar(value=max(100, min(8000, float(cfg.get("item_match_threshold", 1500)))))
        v["item_margin"] = tk.DoubleVar(value=max(0, min(1000, float(cfg.get("item_match_margin", 200)))))
        v["grid_x1"] = tk.StringVar(value=str(grid.get("x1", 798)))
        v["grid_y1"] = tk.StringVar(value=str(grid.get("y1", 351)))
        v["grid_x2"] = tk.StringVar(value=str(grid.get("x2", 1122)))
        v["grid_y2"] = tk.StringVar(value=str(grid.get("y2", 567)))
        v["grid_cols"] = tk.StringVar(value=str(grid.get("columns", 9)))
        v["grid_rows"] = tk.StringVar(value=str(grid.get("rows", 6)))
        icon = cfg.get("icon_roi_in_slot", paths.DEFAULT_CONFIG["icon_roi_in_slot"])
        count = cfg.get("count_roi_in_slot", paths.DEFAULT_CONFIG["count_roi_in_slot"])
        for i in range(4):
            v[f"icon_{i}"] = tk.StringVar(value=str(icon[i] if i < len(icon) else 0))
            v[f"count_{i}"] = tk.StringVar(value=str(count[i] if i < len(count) else 0))
        v["chest_template"] = tk.StringVar(value=str(cfg.get("chest_template", "assets/chest_open.png")))
        v["empty_template"] = tk.StringVar(value=str(cfg.get("empty_slot_template", "assets/empty_slot.png")))
        v["webhook"] = tk.StringVar(value=self.reader.get_discord_webhook_url() or "")
        v["discord_env"] = tk.StringVar(value=str(cfg.get("discord_webhook_env", "DISCORD_WEBHOOK_URL")))
        v["max_items"] = tk.StringVar(value=str(cfg.get("discord_max_items_in_message", 25)))
        v["delete_old"] = tk.BooleanVar(value=bool(cfg.get("discord_delete_old_messages", True)))
        v["stack_size"] = tk.StringVar(value=str(dm.get("stack_size", 64)))
        hk = cfg.get("hotkeys", {}) if isinstance(cfg.get("hotkeys"), dict) else {}
        for action in ("digits", "grid", "scan", "discord", "exit"):
            v[f"hk_{action}"] = tk.StringVar(value=str(hk.get(action, "") or ""))
        self._settings_vars = v

    def _settings_page_general(self, parent):
        v = self._settings_vars
        card = self._settings_card(parent, "Эталонное разрешение",
                                   "Сетка масштабируется от этого разрешения.")
        row = tk.Frame(card, bg=self.PANEL)
        row.pack(fill="x")
        for label, key in [("Ширина", "ref_w"), ("Высота", "ref_h")]:
            tk.Label(row, text=label, bg=self.PANEL, fg=self.MUTED,
                     font=("Segoe UI", 10)).pack(side="left", padx=(0, 8))
            tk.Entry(row, textvariable=v[key], bg=self.PANEL_2, fg=self.TEXT,
                     insertbackground=self.TEXT, relief="flat", width=10,
                     justify="right", font=("Segoe UI", 10)).pack(side="left", ipady=5, padx=(0, 24))
        card2 = self._settings_card(parent, "Сканирование")
        self._settings_scale(card2, "Интервал автосканирования, сек.", v["scan_interval"], 0.5, 10.0, "{:.1f}")
        self._settings_check(card2, "Автосканирование при запуске", v["auto_scan"])
        self._settings_check(card2, "Показывать кандидатов в логе", v["print_candidates"])

        card3 = self._settings_card(parent, "Горячие клавиши",
                                    "Пустое поле отключает действие. "
                                    "Имена клавиш: f1–f12, цифры, буквы.")
        for label, key in [("Цифры (отладка)", "hk_digits"), ("Сетка (отладка)", "hk_grid"),
                           ("Сканирование", "hk_scan"), ("Discord", "hk_discord"),
                           ("Выход", "hk_exit")]:
            self._settings_entry(card3, label, v[key], width=16)

    def _settings_page_detection(self, parent):
        v = self._settings_vars
        card = self._settings_card(parent, "Пороги распознавания (SSIM)",
                                   "Меньше расстояние — сильнее похожи изображения.")
        self._settings_scale(card, "Порог шаблона сундука", v["chest_threshold"], 0.5, 0.99, "{:.2f}")
        self._settings_scale(card, "Порог пустого слота", v["empty_threshold"], 100, 5000, "{:.0f}")
        self._settings_scale(card, "Порог совпадения предмета", v["item_threshold"], 100, 8000, "{:.0f}")
        self._settings_scale(card, "Порог неоднозначности", v["item_margin"], 0, 1000, "{:.0f}")

    def _settings_page_grid(self, parent):
        v = self._settings_vars
        card = self._settings_card(parent, "Область сундука (два угла)",
                                   "Левый верхний (x1, y1) и правый нижний (x2, y2).")
        grid_fr = tk.Frame(card, bg=self.PANEL)
        grid_fr.pack(fill="x")
        fields = [("X1 (лево)", "grid_x1"), ("Y1 (верх)", "grid_y1"),
                  ("X2 (право)", "grid_x2"), ("Y2 (низ)", "grid_y2"),
                  ("Столбцы", "grid_cols"), ("Строки", "grid_rows")]
        for idx, (label, key) in enumerate(fields):
            r, c = divmod(idx, 2)
            tk.Label(grid_fr, text=label, bg=self.PANEL, fg=self.MUTED,
                     font=("Segoe UI", 10)).grid(row=r, column=c * 2, sticky="w", padx=(0, 6), pady=4)
            tk.Entry(grid_fr, textvariable=v[key], bg=self.PANEL_2, fg=self.TEXT,
                     insertbackground=self.TEXT, relief="flat", width=10,
                     justify="right", font=("Segoe UI", 10)).grid(row=r, column=c * 2 + 1, padx=(0, 24), ipady=5)
        btns = tk.Frame(card, bg=self.PANEL)
        btns.pack(fill="x", pady=(12, 0))
        self._button(btns, "Калибровка кликами", self._calibrate).pack(side="left")
        self._button(btns, "Отладка сетки", self._run_grid_debug).pack(side="left", padx=8)

        row = tk.Frame(parent, bg=self.BG)
        row.pack(fill="x", pady=(12, 0))
        lf = tk.Frame(row, bg=self.BG)
        lf.pack(side="left", fill="both", expand=True, padx=(0, 6))
        rf = tk.Frame(row, bg=self.BG)
        rf.pack(side="right", fill="both", expand=True, padx=(6, 0))
        ci = self._settings_card(lf, "Иконка предмета", "Доли от размера слота (0.0–1.0).")
        cc = self._settings_card(rf, "Количество", "Область цифр в углу слота.")
        for i, label in enumerate(["X", "Y", "Ширина", "Высота"]):
            self._settings_entry(ci, label, v[f"icon_{i}"], width=10)
            self._settings_entry(cc, label, v[f"count_{i}"], width=10)

    def _settings_page_templates(self, parent):
        v = self._settings_vars
        card = self._settings_card(parent, "Шаблон сундука",
                                   "Файл для определения открытого сундука.")
        self._template_selector(card, v["chest_template"], "Шаблон сундука")
        card2 = self._settings_card(parent, "Шаблон пустого слота",
                                    "Файл для определения пустых ячеек.")
        self._template_selector(card2, v["empty_template"], "Пустой слот")
        card3 = self._settings_card(parent, "Автогенерация",
                                    "Создаёт шаблоны из текущего экрана. Сундук должен быть открыт.")
        btn_row = tk.Frame(card3, bg=self.PANEL)
        btn_row.pack(fill="x")
        self._button(btn_row, "Создать недостающие", self._auto_templates).pack(side="left")

    def _settings_page_discord(self, parent):
        v = self._settings_vars
        card = self._settings_card(parent, "Webhook")
        row = tk.Frame(card, bg=self.PANEL)
        row.pack(fill="x")
        tk.Label(row, text="URL", bg=self.PANEL, fg=self.MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        tk.Entry(row, textvariable=v["webhook"], bg=self.PANEL_2, fg=self.TEXT,
                 insertbackground=self.TEXT, relief="flat", font=("Consolas", 10)).pack(
            side="left", fill="x", expand=True, padx=(8, 8), ipady=5)
        status = tk.Label(card, text="", bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 9))
        status.pack(anchor="w", pady=(4, 0))

        def test():
            url = v["webhook"].get().strip()
            if not url or not url.startswith(("https://discord.com/api/webhooks/",
                                              "https://discordapp.com/api/webhooks/")):
                status.configure(text="Некорректный URL", fg=self.RED)
                return
            status.configure(text="Проверка...", fg=self.YELLOW)

            def worker():
                try:
                    r = requests.get(url, timeout=10)
                    ok = r.status_code == 200
                    msg = "Webhook доступен." if ok else f"HTTP {r.status_code}"
                except requests.RequestException as e:
                    ok, msg = False, str(e)

                def apply():
                    try:
                        if self._settings_win and self._settings_win.winfo_exists():
                            status.configure(text=msg, fg=self.GREEN if ok else self.RED)
                    except tk.TclError:
                        pass

                try:
                    self.root.after(0, apply)
                except Exception:
                    pass

            threading.Thread(target=worker, daemon=True).start()

        self._button(card, "Проверить", test).pack(anchor="w", pady=(8, 0))

        card2 = self._settings_card(parent, "Параметры отчёта")
        self._settings_entry(card2, "Размер стака по умолчанию", v["stack_size"], width=10)
        self._settings_entry(card2, "Максимум предметов", v["max_items"], width=10)
        self._settings_check(card2, "Удалять лишние старые сообщения", v["delete_old"])
        self._settings_entry(card2, "Переменная окружения", v["discord_env"], width=28)

    def _save_settings_from_gui(self):
        win = self._settings_win
        v = self._settings_vars
        try:
            rw = self._parse_int(v["ref_w"], "Ширина")
            rh = self._parse_int(v["ref_h"], "Высота")
            if rw < 320 or rh < 240:
                raise ValueError("Разрешение слишком маленькое.")
            si = float(v["scan_interval"].get())
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
            for action in ("digits", "grid", "scan", "discord", "exit"):
                hotkeys[action] = v[f"hk_{action}"].get().strip().lower()
        except ValueError as e:
            messagebox.showerror("Настройки", str(e), parent=win)
            return

        webhook = v["webhook"].get().strip()
        if webhook and not webhook.startswith(("https://discord.com/api/webhooks/",
                                               "https://discordapp.com/api/webhooks/")):
            messagebox.showerror("Discord", "Некорректный Webhook URL.", parent=win)
            return

        cfg = self.reader.config
        cfg["reference_screen_size"] = [rw, rh]
        cfg["scan_interval_seconds"] = si
        cfg["print_item_candidates"] = bool(v["print_candidates"].get())
        cfg["auto_scan_enabled"] = bool(v["auto_scan"].get())
        cfg["chest_template_threshold"] = float(v["chest_threshold"].get())
        cfg["empty_slot_threshold"] = float(v["empty_threshold"].get())
        cfg["item_match_threshold"] = float(v["item_threshold"].get())
        cfg["item_match_margin"] = float(v["item_margin"].get())
        cfg["chest_grid"] = grid
        cfg["icon_roi_in_slot"] = icon
        cfg["count_roi_in_slot"] = count
        cfg["chest_template"] = v["chest_template"].get().strip() or "assets/chest_open.png"
        cfg["empty_slot_template"] = v["empty_template"].get().strip() or "assets/empty_slot.png"
        cfg["discord_webhook_env"] = v["discord_env"].get().strip() or "DISCORD_WEBHOOK_URL"
        cfg["discord_max_items_in_message"] = max_items
        cfg["discord_delete_old_messages"] = bool(v["delete_old"].get())
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
        was_auto = self.reader.auto_scan_active
        if was_auto:
            self.reader.stop_auto_scan()
        self.reader.reload_profile()
        if cfg.get("auto_scan_enabled", False):
            self.reader.start_auto_scan()
        self.auto_scan_btn.configure(
            text="⏸ Автоскан" if self.reader.auto_scan_active else "▶ Автоскан",
            bg=self.GREEN if self.reader.auto_scan_active else self.PANEL_2)
        self._refresh_profile_label()
        self._refresh_stats()
        try:
            self.hotkeys_hint_label.configure(text=self._hotkeys_hint())
        except Exception:
            pass
        self._log("✓ Настройки сохранены.", self.GREEN)
        try:
            win.grab_release()
            win.destroy()
        except tk.TclError:
            pass
