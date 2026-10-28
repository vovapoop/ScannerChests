"""GUI mixin: все диалоговые окна приложения."""
import json
import os
import shutil
import threading
import tkinter as tk
import uuid
from datetime import datetime
from tkinter import filedialog, messagebox, simpledialog, ttk

from chest_scanner.commons import absolute_path, load_json, save_json
from chest_scanner.config import activate_profile, load_profiles, profile_entry, save_profiles
from chest_scanner.export import export_chests_to_csv, export_chests_to_json, export_summary_to_csv
from chest_scanner.items import copy_image_as_png, next_image_path, normalize_items_db, sanitize_filename
from chest_scanner.logging_setup import logger
from chest_scanner import paths


class GuiDialogsMixin:
    # ── Мастер первого запуска ──
    def _safe_first_run_wizard(self):
        try:
            self._first_run_wizard()
        except Exception as e:
            logger.error(f"Мастер первого запуска: {e}", exc_info=True)
            try:
                self.root.deiconify()
                self.root.lift()
                self.root.focus_force()
            except Exception:
                pass

    def _first_run_wizard(self):
        dialog = self._modal("Первый запуск", "700x480")
        state = {"page": 0, "profile": load_profiles().get("active", "Default"),
                 "webhook": self.reader.get_discord_webhook_url() or ""}
        tk.Label(dialog, text="Настройка Chest Scanner", bg=self.BG, fg=self.TEXT,
                 font=("Segoe UI", 19, "bold")).pack(anchor="w", padx=28, pady=(24, 5))
        tk.Label(dialog, text="Выберите профиль и настройте Discord.",
                 bg=self.BG, fg=self.MUTED, font=("Segoe UI", 10)).pack(anchor="w", padx=28)
        body = tk.Frame(dialog, bg=self.BG)
        body.pack(fill="both", expand=True, padx=28, pady=22)
        nav = tk.Frame(dialog, bg=self.BG)
        nav.pack(fill="x", padx=28, pady=(0, 22))
        pv = tk.StringVar(value=state["profile"])
        wv = tk.StringVar(value=state["webhook"])

        def finish():
            name = pv.get().strip() or "Default"
            if any(c in name for c in '/\\:*?"<>|'):
                messagebox.showerror("Профиль", "Недопустимые символы.", parent=dialog)
                return
            data = load_profiles()
            if name not in {e.get("name") for e in data.get("profiles", [])}:
                profile_entry(name)
            activate_profile(name)
            self.reader.reload_profile()
            wh = wv.get().strip()
            if wh:
                save_json(paths.DISCORD_WEBHOOK_FILE, {"webhook_url": wh})
            self.reader.config["profile_name"] = name
            self.reader.config["setup_complete"] = True
            save_json(paths.CONFIG_FILE, self.reader.config)
            data = load_profiles()
            data["active"] = name
            data["setup_complete"] = True
            save_profiles(data)
            self._refresh_profile_label()
            self._refresh_stats()
            try:
                dialog.grab_release()
            except tk.TclError:
                pass
            dialog.destroy()
            self._log("✓ Настройка завершена.", self.GREEN)

        def render(idx):
            for c in body.winfo_children():
                c.destroy()
            state["page"] = idx
            if idx == 0:
                tk.Label(body, text="1. Профиль", bg=self.BG, fg=self.ACCENT_HOVER,
                         font=("Segoe UI", 11, "bold")).pack(anchor="w")
                tk.Label(body, text="Имя профиля для отдельной базы и результатов.",
                         bg=self.BG, fg=self.TEXT, wraplength=620, justify="left",
                         font=("Segoe UI", 11)).pack(anchor="w", pady=(12, 10))
                e = tk.Entry(body, textvariable=pv, bg=self.PANEL_2, fg=self.TEXT,
                             insertbackground=self.TEXT, relief="flat", font=("Segoe UI", 11))
                e.pack(fill="x", ipady=9)
                e.focus_set()
            elif idx == 1:
                tk.Label(body, text="2. Discord Webhook", bg=self.BG, fg=self.ACCENT_HOVER,
                         font=("Segoe UI", 11, "bold")).pack(anchor="w")
                tk.Label(body, text="Можно настроить позже через «Настройки».",
                         bg=self.BG, fg=self.TEXT, wraplength=620, justify="left",
                         font=("Segoe UI", 11)).pack(anchor="w", pady=(12, 10))
                e = tk.Entry(body, textvariable=wv, bg=self.PANEL_2, fg=self.TEXT,
                             insertbackground=self.TEXT, relief="flat", font=("Segoe UI", 11))
                e.pack(fill="x", ipady=9)
                e.focus_set()
            else:
                tk.Label(body, text="3. Готово", bg=self.BG, fg=self.GREEN,
                         font=("Segoe UI", 11, "bold")).pack(anchor="w")
                tk.Label(body, text="Настройки сохранены.\nОткройте сундук и нажмите F8.",
                         bg=self.BG, fg=self.TEXT, wraplength=620, justify="left",
                         font=("Segoe UI", 11)).pack(anchor="w", pady=(14, 6))
            bb.configure(state="normal" if idx > 0 else "disabled")
            nb.configure(text="Готово" if idx == 2 else "Далее")

        def go_next():
            if state["page"] == 2:
                finish()
            else:
                render(state["page"] + 1)

        bb = tk.Button(nav, text="Назад",
                       command=lambda: render(state["page"] - 1) if state["page"] > 0 else None,
                       bg=self.PANEL_2, fg=self.TEXT, relief="flat", bd=0, padx=16, pady=8,
                       font=("Segoe UI", 9, "bold"))
        bb.pack(side="left")
        nb = tk.Button(nav, text="Далее", command=go_next,
                       bg=self.ACCENT, fg=self.TEXT, relief="flat", bd=0, padx=20, pady=8,
                       font=("Segoe UI", 9, "bold"))
        nb.pack(side="right")
        dialog.bind("<Return>", lambda _e: go_next())
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        render(0)
        dialog.wait_window(dialog)

    # ── Неизвестный предмет ──
    def _prompt_unknown_item(self, unknown_name, image_path):
        result = {"name": ""}
        done = threading.Event()

        def show():
            saved = self._release_all_grabs()
            dialog = self._modal("Новый предмет", "680x420")
            tk.Label(dialog, text="Найден новый предмет", bg=self.BG, fg=self.TEXT,
                     font=("Segoe UI", 17, "bold")).pack(anchor="w", padx=24, pady=(22, 4))
            body = tk.Frame(dialog, bg=self.BG)
            body.pack(fill="both", expand=True, padx=24, pady=(10, 0))
            icf = tk.Frame(body, bg=self.PANEL, highlightbackground=self.BORDER,
                           highlightthickness=1, width=140, height=140)
            icf.pack(side="left", anchor="n", padx=(0, 20))
            icf.pack_propagate(False)
            icl = tk.Label(icf, text="Нет\nиконки", bg=self.PANEL, fg=self.MUTED, font=("Segoe UI", 9))
            icl.pack(expand=True)
            try:
                if image_path and os.path.isfile(image_path):
                    icon = tk.PhotoImage(file=image_path)
                    if icon.width() <= 48 and icon.height() <= 48:
                        icon = icon.zoom(3, 3)
                    icl.configure(image=icon, text="")
                    icl.image = icon
            except Exception:
                pass
            info = tk.Frame(body, bg=self.BG)
            info.pack(side="left", fill="both", expand=True)
            tk.Label(info, text=f"Шаблон: {unknown_name}", bg=self.BG, fg=self.MUTED,
                     font=("Segoe UI", 9)).pack(anchor="w")
            tk.Label(info, text="Название предмета:", bg=self.BG, fg=self.TEXT,
                     font=("Segoe UI", 10)).pack(anchor="w", pady=(18, 6))
            entry = tk.Entry(info, bg=self.PANEL_2, fg=self.TEXT, insertbackground=self.TEXT,
                             relief="flat", font=("Segoe UI", 11))
            entry.pack(fill="x", ipady=8)
            entry.focus_set()

            # Выбор категории
            tk.Label(info, text="Категория:", bg=self.BG, fg=self.TEXT,
                     font=("Segoe UI", 10)).pack(anchor="w", pady=(12, 4))
            cat_var = tk.StringVar(value="Без категории")
            combo = ttk.Combobox(info, textvariable=cat_var,
                                 values=[c[1] for c in paths.ITEM_CATEGORIES], state="readonly")
            combo.pack(fill="x")

            btns = tk.Frame(dialog, bg=self.BG)
            btns.pack(fill="x", padx=24, pady=18)

            def fin():
                result["name"] = entry.get().strip()
                selected = cat_var.get()
                cat_key = "uncategorized"
                for key, title in paths.ITEM_CATEGORIES:
                    if title == selected:
                        cat_key = key
                        break
                self.reader.unknown_item_category = cat_key
                try:
                    dialog.grab_release()
                except tk.TclError:
                    pass
                dialog.destroy()
                self._restore_grabs(saved)
                done.set()

            tk.Button(btns, text="Добавить в базу", command=fin, bg=self.ACCENT, fg=self.TEXT,
                      relief="flat", bd=0, padx=18, pady=8, font=("Segoe UI", 10, "bold")).pack(side="right")
            tk.Button(btns, text="Оставить unknown", command=fin, bg=self.PANEL_2, fg=self.TEXT,
                      relief="flat", bd=0, padx=14, pady=8, font=("Segoe UI", 9)).pack(side="right", padx=(0, 8))
            dialog.bind("<Return>", lambda _e: fin())
            dialog.bind("<Escape>", lambda _e: fin())
            dialog.protocol("WM_DELETE_WINDOW", fin)

        try:
            self.root.after(0, show)
        except Exception:
            done.set()
            return ""
        done.wait()
        return result["name"]

    # ── История ──
    def _load_all_chests(self):
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

    def open_history_dialog(self):
        dialog = self._modal("История сканирований", "1100x700", (950, 600))
        tk.Label(dialog, text="История сканирований", bg=self.BG, fg=self.TEXT,
                 font=("Segoe UI", 19, "bold")).pack(anchor="w", padx=20, pady=(18, 4))

        # Фильтры
        ff = tk.Frame(dialog, bg=self.PANEL, highlightbackground=self.BORDER, highlightthickness=1)
        ff.pack(fill="x", padx=20, pady=(8, 0))
        fi = tk.Frame(ff, bg=self.PANEL)
        fi.pack(fill="x", padx=14, pady=10)

        tk.Label(fi, text="Предмет:", bg=self.PANEL, fg=self.MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        filter_item = tk.StringVar()
        tk.Entry(fi, textvariable=filter_item, bg=self.PANEL_2, fg=self.TEXT,
                 insertbackground=self.TEXT, relief="flat", width=18,
                 font=("Segoe UI", 9)).pack(side="left", padx=(4, 16), ipady=4)

        tk.Label(fi, text="Мин. кол-во:", bg=self.PANEL, fg=self.MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        filter_qty = tk.StringVar()
        tk.Entry(fi, textvariable=filter_qty, bg=self.PANEL_2, fg=self.TEXT,
                 insertbackground=self.TEXT, relief="flat", width=8,
                 font=("Segoe UI", 9)).pack(side="left", padx=(4, 16), ipady=4)

        tk.Label(fi, text="Дата от:", bg=self.PANEL, fg=self.MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        filter_from = tk.StringVar()
        tk.Entry(fi, textvariable=filter_from, bg=self.PANEL_2, fg=self.TEXT,
                 insertbackground=self.TEXT, relief="flat", width=12,
                 font=("Segoe UI", 9)).pack(side="left", padx=(4, 16), ipady=4)

        tk.Label(fi, text="до:", bg=self.PANEL, fg=self.MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        filter_to = tk.StringVar()
        tk.Entry(fi, textvariable=filter_to, bg=self.PANEL_2, fg=self.TEXT,
                 insertbackground=self.TEXT, relief="flat", width=12,
                 font=("Segoe UI", 9)).pack(side="left", padx=(4, 8), ipady=4)

        def clear_filters():
            filter_item.set("")
            filter_qty.set("")
            filter_from.set("")
            filter_to.set("")
            refresh_list()

        self._button(fi, "Применить", lambda: refresh_list()).pack(side="right", padx=3)
        self._button(fi, "Сбросить", clear_filters).pack(side="right", padx=3)

        # Контент
        content = tk.Frame(dialog, bg=self.BG)
        content.pack(fill="both", expand=True, padx=20, pady=(10, 12))

        left = tk.Frame(content, bg=self.PANEL, highlightbackground=self.BORDER, highlightthickness=1)
        left.pack(side="left", fill="both", expand=False, padx=(0, 10))
        left.configure(width=380)

        tk.Label(left, text="Сканирования", bg=self.PANEL, fg=self.MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=12, pady=(10, 4))

        listbox = tk.Listbox(left, bg=self.PANEL, fg=self.TEXT, selectbackground="#29304a",
                             selectforeground=self.TEXT, relief="flat", highlightthickness=0,
                             font=("Segoe UI", 10), activestyle="none", selectmode="extended")
        listbox.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        right = tk.Frame(content, bg=self.PANEL, highlightbackground=self.BORDER, highlightthickness=1)
        right.pack(side="right", fill="both", expand=True)

        detail_header = tk.Label(right, text="Выберите сканирование", bg=self.PANEL,
                                 fg=self.MUTED, font=("Segoe UI", 11))
        detail_header.pack(anchor="w", padx=16, pady=(12, 4))

        detail_info = tk.Label(right, text="", bg=self.PANEL, fg=self.TEXT,
                               font=("Segoe UI", 9), justify="left")
        detail_info.pack(anchor="w", padx=16, pady=(0, 8))

        table_frame = tk.Frame(right, bg=self.PANEL)
        table_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        table = ttk.Treeview(table_frame, columns=("item", "qty", "stacks", "category"),
                             show="headings", selectmode="browse")
        for col, title, width, anchor in [
            ("item", "Предмет", 280, "w"), ("qty", "Кол-во", 80, "center"),
            ("stacks", "Стаков", 70, "center"), ("category", "Категория", 140, "w")]:
            table.heading(col, text=title)
            table.column(col, width=width, anchor=anchor)
        table.pack(fill="both", expand=True, side="left")
        table_scroll = tk.Scrollbar(table_frame, command=table.yview)
        table_scroll.pack(side="right", fill="y")
        table.configure(yscrollcommand=table_scroll.set)

        # Кнопки
        bottom = tk.Frame(dialog, bg=self.BG)
        bottom.pack(fill="x", padx=20, pady=(0, 16))

        all_chests = self._load_all_chests()
        filtered = []
        state = {"selected": None}

        def parse_date(text):
            text = text.strip()
            if not text:
                return None
            for fmt in ("%d.%m.%Y", "%d.%m"):
                try:
                    return datetime.strptime(text, fmt)
                except ValueError:
                    continue
            return None

        def apply_filters(chests):
            item_f = filter_item.get().strip().lower()
            qty_f = 0
            try:
                qty_f = int(filter_qty.get().strip() or "0")
            except ValueError:
                pass
            d_from = parse_date(filter_from.get())
            d_to = parse_date(filter_to.get())
            result = []
            for ch in chests:
                ts = ch.get("timestamp", "")
                totals = ch.get("totals", {})
                if item_f:
                    if not any(item_f in n.lower() for n in totals):
                        continue
                if qty_f > 0:
                    if sum(totals.values()) < qty_f:
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

        def refresh_list():
            nonlocal filtered
            filtered = apply_filters(all_chests)
            listbox.delete(0, "end")
            for ch in filtered:
                ts = ch.get("timestamp", "?")
                slots = ch.get("occupied_slots", 0)
                items = ch.get("unique_items", 0)
                src = ch.get("_source", "")
                label = f"{ts}  |  {slots} ячеек  |  {items} предм."
                if src:
                    label += f"  [{src[:12]}]"
                listbox.insert("end", label)
            detail_header.configure(text="Выберите сканирование")
            detail_info.configure(text="")
            for iid in table.get_children():
                table.delete(iid)
            state["selected"] = None

        def show_details(event=None):
            sel = listbox.curselection()
            if not sel:
                return
            idx = sel[0]
            state["selected"] = idx
            if idx >= len(filtered):
                return
            ch = filtered[idx]
            ts = ch.get("timestamp", "?")
            detail_header.configure(text=f"Сканирование от {ts}")
            res = ch.get("screen_resolution", [0, 0])
            info = (f"Ячеек занято: {ch.get('occupied_slots', 0)}    "
                    f"Уникальных: {ch.get('unique_items', 0)}    "
                    f"Разрешение: {res[0]}x{res[1]}")
            unknowns = ch.get("unknown_items", [])
            if unknowns:
                info += f"\nНеизвестные: {', '.join(unknowns)}"
            detail_info.configure(text=info)
            for iid in table.get_children():
                table.delete(iid)
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
                table.insert("", "end", values=(name, qty, stacks, cat_name))

        listbox.bind("<<ListboxSelect>>", show_details)

        def compare():
            sel = listbox.curselection()
            if len(sel) < 2:
                messagebox.showinfo("Сравнение",
                                    "Выберите два сканирования.\nУдерживайте Ctrl при клике.",
                                    parent=dialog)
                return
            i1, i2 = sel[0], sel[1]
            if i1 >= len(filtered) or i2 >= len(filtered):
                return
            self._show_comparison(filtered[i1], filtered[i2], dialog)

        self._button(bottom, "Сравнить выбранные", compare).pack(side="left", padx=3)
        tk.Label(bottom, text=f"Всего сканирований: {len(all_chests)}",
                 bg=self.BG, fg=self.MUTED, font=("Segoe UI", 9)).pack(side="right")
        self._button(bottom, "Закрыть", dialog.destroy).pack(side="right", padx=3)
        refresh_list()

    def _show_comparison(self, ch1, ch2, parent=None):
        dialog = tk.Toplevel(parent or self.root)
        dialog.title("Сравнение сканирований")
        dialog.geometry("800x600")
        dialog.minsize(700, 500)
        dialog.configure(bg=self.BG)
        try:
            dialog.transient(parent or self.root)
            dialog.grab_set()
        except tk.TclError:
            pass

        ts1, ts2 = ch1.get("timestamp", "?"), ch2.get("timestamp", "?")
        tk.Label(dialog, text="Сравнение сканирований", bg=self.BG, fg=self.TEXT,
                 font=("Segoe UI", 18, "bold")).pack(anchor="w", padx=24, pady=(18, 4))
        tk.Label(dialog, text=f"Скан 1: {ts1}    ↔    Скан 2: {ts2}",
                 bg=self.BG, fg=self.MUTED, font=("Segoe UI", 10)).pack(anchor="w", padx=24, pady=(0, 12))

        t1, t2 = ch1.get("totals", {}), ch2.get("totals", {})
        all_items = sorted(set(list(t1.keys()) + list(t2.keys())), key=lambda x: x.lower())
        comparison = []
        for item in all_items:
            q1, q2 = t1.get(item, 0), t2.get(item, 0)
            diff = q2 - q1
            status = "added" if diff > 0 else ("removed" if diff < 0 else "same")
            comparison.append({"item": item, "q1": q1, "q2": q2, "diff": diff, "status": status})

        tf = tk.Frame(dialog, bg=self.BG)
        tf.pack(fill="both", expand=True, padx=24, pady=(0, 12))

        ct = ttk.Treeview(tf, columns=("item", "q1", "q2", "diff", "status"),
                          show="headings", selectmode="browse")
        for col, title, width, anchor in [
            ("item", "Предмет", 260, "w"), ("q1", "Скан 1", 90, "center"),
            ("q2", "Скан 2", 90, "center"), ("diff", "Разница", 90, "center"),
            ("status", "Статус", 120, "center")]:
            ct.heading(col, text=title)
            ct.column(col, width=width, anchor=anchor)
        ct.pack(fill="both", expand=True, side="left")
        cs = tk.Scrollbar(tf, command=ct.yview)
        cs.pack(side="right", fill="y")
        ct.configure(yscrollcommand=cs.set)
        ct.tag_configure("added", foreground="#35c98b")
        ct.tag_configure("removed", foreground="#f25f67")
        ct.tag_configure("same", foreground="#8f9aaa")

        for e in comparison:
            d = e["diff"]
            if e["status"] == "added":
                dt, st = f"+{d}", "Добавилось"
            elif e["status"] == "removed":
                dt, st = str(d), "Убавилось"
            else:
                dt, st = "0", "Без изменений"
            ct.insert("", "end", values=(e["item"], e["q1"], e["q2"], dt, st), tags=(e["status"],))

        added = [e for e in comparison if e["status"] == "added"]
        removed = [e for e in comparison if e["status"] == "removed"]
        same = [e for e in comparison if e["status"] == "same"]
        total_diff = sum(e["diff"] for e in comparison)

        summary = tk.Frame(dialog, bg=self.BG)
        summary.pack(fill="x", padx=24, pady=(0, 8))
        tk.Label(summary, text=f"Добавилось: {len(added)}    Убавилось: {len(removed)}    "
                               f"Без изменений: {len(same)}",
                 bg=self.BG, fg=self.MUTED, font=("Segoe UI", 10)).pack(side="left")
        dc = self.GREEN if total_diff >= 0 else self.RED
        ds = "+" if total_diff >= 0 else ""
        tk.Label(summary, text=f"Общая разница: {ds}{total_diff}",
                 bg=self.BG, fg=dc, font=("Segoe UI", 10, "bold")).pack(side="right")
        self._button(dialog, "Закрыть", dialog.destroy).pack(pady=14)

    # ── Экспорт ──
    def open_export_dialog(self):
        dialog = self._modal("Экспорт данных", "620x420")
        tk.Label(dialog, text="Экспорт данных", bg=self.BG, fg=self.TEXT,
                 font=("Segoe UI", 19, "bold")).pack(anchor="w", padx=24, pady=(22, 4))
        data = load_json(paths.RESULTS_FILE, {"chests": []})
        chests = data.get("chests", []) if isinstance(data, dict) else []
        tk.Label(dialog, text=f"Доступно сканирований: {len(chests)}",
                 bg=self.BG, fg=self.MUTED, font=("Segoe UI", 10)).pack(anchor="w", padx=24, pady=(0, 16))

        def do_export(export_type, fmt):
            if not chests:
                messagebox.showinfo("Экспорт", "Нет данных.", parent=dialog)
                return
            ext = ".csv" if fmt == "csv" else ".json"
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = filedialog.asksaveasfilename(
                parent=dialog, defaultextension=ext,
                filetypes=[(fmt.upper(), f"*{ext}")],
                initialfile=f"chest_export_{ts}{ext}")
            if not path:
                return
            try:
                if fmt == "json":
                    export_chests_to_json(chests, path)
                elif export_type == "summary":
                    export_summary_to_csv(chests, path)
                else:
                    export_chests_to_csv(chests, path)
                self._log(f"✓ Экспортировано: {os.path.basename(path)}", self.GREEN)
                messagebox.showinfo("Экспорт", f"Сохранено:\n{path}", parent=dialog)
            except Exception as e:
                messagebox.showerror("Экспорт", str(e), parent=dialog)

        for title, etype, fmts in [
            ("Полная выгрузка (все слоты)", "full", ["csv", "json"]),
            ("Сводная таблица (итоги)", "summary", ["csv"])]:
            fr = tk.Frame(dialog, bg=self.BG)
            fr.pack(fill="x", padx=24, pady=10)
            tk.Label(fr, text=title, bg=self.BG, fg=self.TEXT,
                     font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 8))
            bf = tk.Frame(fr, bg=self.BG)
            bf.pack(anchor="w")
            for fmt in fmts:
                self._button(bf, fmt.upper(), lambda t=etype, f=fmt: do_export(t, f)).pack(side="left", padx=(0, 8))
        self._button(dialog, "Закрыть", dialog.destroy).pack(pady=18)

    # ── База предметов ──
    def open_item_database(self):
        dialog = self._modal("База предметов", "1100x650", (950, 560))
        tk.Label(dialog, text="База предметов", bg=self.BG, fg=self.TEXT,
                 font=("Segoe UI", 19, "bold")).pack(anchor="w", padx=20, pady=(18, 4))

        top = tk.Frame(dialog, bg=self.BG)
        top.pack(fill="x", padx=20, pady=(0, 10))
        search_var = tk.StringVar()
        tk.Label(top, text="Поиск", bg=self.BG, fg=self.MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        tk.Entry(top, textvariable=search_var, bg=self.PANEL_2, fg=self.TEXT,
                 insertbackground=self.TEXT, relief="flat", font=("Segoe UI", 10)).pack(
            side="left", fill="x", expand=True, padx=(10, 10), ipady=6)
        cat_var = tk.StringVar(value="Все категории")
        cat_combo = ttk.Combobox(top, textvariable=cat_var,
                                 values=["Все категории"] + [c[1] for c in paths.ITEM_CATEGORIES],
                                 state="readonly", width=22)
        cat_combo.pack(side="left")

        content = tk.Frame(dialog, bg=self.BG)
        content.pack(fill="both", expand=True, padx=20, pady=(0, 12))

        left = tk.Frame(content, bg=self.PANEL, highlightbackground=self.BORDER, highlightthickness=1)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))

        tree = ttk.Treeview(left, columns=("name", "category", "stack", "photos", "enabled"),
                            show="headings", selectmode="browse")
        for col, title, width in [("name", "Название", 280), ("category", "Категория", 130),
                                 ("stack", "Стак", 60), ("photos", "Фото", 60), ("enabled", "Вкл.", 55)]:
            tree.heading(col, text=title)
            tree.column(col, width=width, anchor="w" if col == "name" else "center")
        tree.pack(fill="both", expand=True, side="left")
        ys = tk.Scrollbar(left, command=tree.yview)
        ys.pack(side="right", fill="y")
        tree.configure(yscrollcommand=ys.set)

        right = tk.Frame(content, bg=self.PANEL, highlightbackground=self.BORDER,
                         highlightthickness=1, width=320)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)
        preview = tk.Label(right, text="Выберите предмет", bg=self.PANEL, fg=self.MUTED,
                           font=("Segoe UI", 10))
        preview.pack(fill="x", pady=(18, 8), padx=14)
        details = tk.Label(right, text="", bg=self.PANEL, fg=self.TEXT, justify="left",
                           font=("Segoe UI", 9), wraplength=280)
        details.pack(fill="x", padx=14)
        photo_list = tk.Listbox(right, bg=self.PANEL_2, fg=self.TEXT, relief="flat",
                                highlightthickness=0, height=8)
        photo_list.pack(fill="both", expand=True, padx=14, pady=14)

        state = {"selected_id": None, "thumbnails": {}}

        def get_items():
            return self.reader.items_db.get("items", [])

        def find_item(iid):
            return next((i for i in get_items() if str(i.get("id")) == str(iid)), None)

        def refresh():
            selected = state.get("selected_id")
            state["thumbnails"] = {}
            for iid in tree.get_children():
                tree.delete(iid)
            q = search_var.get().strip().lower()
            cat_idx = cat_combo.current()
            cat_filter = paths.CATEGORY_KEYS[cat_idx - 1] if cat_idx > 0 else None
            for item in sorted(get_items(), key=lambda x: x.get("name", "").lower()):
                if q and q not in item.get("name", "").lower():
                    continue
                if cat_filter and item.get("category", "uncategorized") != cat_filter:
                    continue
                images = item.get("images") or ([item.get("image")] if item.get("image") else [])
                thumbnail = None
                if images:
                    try:
                        tp = absolute_path(images[0])
                        if tp and os.path.isfile(tp):
                            img = tk.PhotoImage(file=tp)
                            sc = max(1, max(img.width() // 32, img.height() // 32))
                            if sc > 1:
                                img = img.subsample(sc, sc)
                            thumbnail = img
                    except Exception:
                        thumbnail = None
                item_id = str(item["id"])
                cat_name = paths.CATEGORY_NAMES.get(item.get("category", "uncategorized"), "?")
                vals = (item.get("name", ""), cat_name, item.get("stack_size", 64),
                        len(images), "Да" if item.get("enabled", True) else "Нет")
                if thumbnail:
                    state["thumbnails"][item_id] = thumbnail
                    tree.insert("", "end", iid=item_id, image=thumbnail, values=vals)
                else:
                    tree.insert("", "end", iid=item_id, values=vals)
            if selected and tree.exists(str(selected)):
                tree.selection_set(str(selected))
                tree.focus(str(selected))
                show_selected()

        def show_selected(_e=None):
            selection = tree.selection()
            if not selection:
                state["selected_id"] = None
                preview.configure(text="Выберите предмет", image="")
                details.configure(text="")
                photo_list.delete(0, "end")
                return
            item = find_item(selection[0])
            if not item:
                return
            state["selected_id"] = item["id"]
            images = item.get("images") or ([item.get("image")] if item.get("image") else [])
            cat_name = paths.CATEGORY_NAMES.get(item.get("category", "uncategorized"), "Без категории")
            tags = ", ".join(item.get("tags", [])) or "—"
            details.configure(text=(
                f"Название: {item.get('name', '')}\n"
                f"Категория: {cat_name}\n"
                f"Теги: {tags}\n"
                f"Стак: {item.get('stack_size', 64)}\n"
                f"Фото: {len(images)}\n"
                f"Включён: {'Да' if item.get('enabled', True) else 'Нет'}"))
            photo_list.delete(0, "end")
            for p in images:
                photo_list.insert("end", p)
            if images:
                try:
                    img = tk.PhotoImage(file=absolute_path(images[0]))
                    sc = max(1, img.width() // 96, img.height() // 96)
                    if sc > 1:
                        img = img.subsample(sc, sc)
                    preview.configure(image=img, text="")
                    preview.image = img
                except Exception:
                    preview.configure(text="Нет предпросмотра", image="")
            else:
                preview.configure(text="Нет фото", image="")

        tree.bind("<<TreeviewSelect>>", show_selected)
        search_var.trace_add("write", lambda *_: refresh())
        cat_combo.bind("<<ComboboxSelected>>", lambda *_: refresh())

        def save_db():
            self.reader.items_db = normalize_items_db(self.reader.items_db)
            save_json(paths.ITEMS_FILE, self.reader.items_db)
            self.reader.items_db = normalize_items_db(load_json(paths.ITEMS_FILE, {"items": []}))
            self.reader.template_cache.invalidate()
            self.reader._rebuild_stack_cache()
            refresh()
            self._refresh_stats()

        def choose_category(initial="uncategorized"):
            cat_dialog = tk.Toplevel(dialog)
            cat_dialog.title("Выбор категории")
            cat_dialog.geometry("380x460")
            cat_dialog.minsize(340, 400)
            cat_dialog.configure(bg=self.BG)
            try:
                cat_dialog.transient(dialog)
                cat_dialog.grab_set()
                cat_dialog.lift()
                cat_dialog.focus_force()
            except tk.TclError:
                pass
            tk.Label(cat_dialog, text="Категория предмета", bg=self.BG, fg=self.TEXT,
                     font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=24, pady=(22, 4))
            tk.Label(cat_dialog, text="Выберите категорию:", bg=self.BG, fg=self.MUTED,
                     font=("Segoe UI", 10)).pack(anchor="w", padx=24, pady=(0, 14))
            selected = tk.StringVar(value=initial)
            list_frame = tk.Frame(cat_dialog, bg=self.PANEL,
                                  highlightbackground=self.BORDER, highlightthickness=1)
            list_frame.pack(fill="both", expand=True, padx=24)
            inner = tk.Frame(list_frame, bg=self.PANEL)
            inner.pack(fill="both", expand=True, padx=10, pady=10)
            for key, title in paths.ITEM_CATEGORIES:
                tk.Radiobutton(inner, text=title, value=key, variable=selected,
                               bg=self.PANEL, fg=self.TEXT, selectcolor=self.PANEL_2,
                               activebackground=self.PANEL, activeforeground=self.TEXT,
                               font=("Segoe UI", 11), anchor="w", padx=6, pady=4).pack(fill="x")
            btns = tk.Frame(cat_dialog, bg=self.BG)
            btns.pack(fill="x", padx=24, pady=18)
            result = {"value": None}

            def confirm():
                result["value"] = selected.get()
                try:
                    cat_dialog.grab_release()
                except tk.TclError:
                    pass
                cat_dialog.destroy()

            def cancel():
                try:
                    cat_dialog.grab_release()
                except tk.TclError:
                    pass
                cat_dialog.destroy()

            tk.Button(btns, text="Отмена", command=cancel, bg=self.PANEL_2, fg=self.TEXT,
                      activebackground=self.BORDER, relief="flat", bd=0, padx=16, pady=8,
                      font=("Segoe UI", 9)).pack(side="right")
            tk.Button(btns, text="Выбрать", command=confirm, bg=self.ACCENT, fg="white",
                      activebackground=self.ACCENT_HOVER, relief="flat", bd=0, padx=20, pady=8,
                      font=("Segoe UI", 10, "bold")).pack(side="right", padx=(0, 8))
            cat_dialog.bind("<Return>", lambda _e: confirm())
            cat_dialog.bind("<Escape>", lambda _e: cancel())
            cat_dialog.protocol("WM_DELETE_WINDOW", cancel)
            dialog.wait_window(cat_dialog)
            return result["value"]

        def add_item():
            name = simpledialog.askstring("Новый предмет", "Название:", parent=dialog)
            if not name or not name.strip():
                return
            name = name.strip()
            st = simpledialog.askstring("Новый предмет", "Размер стака:",
                                        initialvalue="64", parent=dialog)
            try:
                stack = max(1, int(st or "64"))
            except ValueError:
                stack = 64
            category = choose_category()
            if category is None:
                category = "uncategorized"
            sel_paths = filedialog.askopenfilenames(
                parent=dialog, title="Фотографии предмета",
                filetypes=[("Изображения", "*.png *.jpg *.jpeg"), ("Все файлы", "*.*")])
            item = {"id": str(uuid.uuid4()), "name": name, "stack_size": stack,
                    "images": [], "image": "", "aliases": [], "enabled": True,
                    "category": category, "tags": []}
            for src in sel_paths:
                rel = next_image_path(name, ".png")
                copy_image_as_png(src, rel)
                item["images"].append(rel)
            if item["images"]:
                item["image"] = item["images"][0]
            self.reader.items_db.setdefault("items", []).append(item)
            save_db()
            tree.selection_set(item["id"])
            self._log(f"✓ Добавлен предмет «{name}».", self.GREEN)

        def edit_item():
            item = find_item(state.get("selected_id"))
            if not item:
                messagebox.showinfo("База", "Выберите предмет.", parent=dialog)
                return
            old_name = item.get("name", "item")
            new_name = simpledialog.askstring("Изменить", "Название:",
                                              initialvalue=old_name, parent=dialog)
            if new_name is None:
                return
            new_name = new_name.strip() or old_name
            st = simpledialog.askstring("Изменить", "Размер стака:",
                                        initialvalue=str(item.get("stack_size", 64)), parent=dialog)
            try:
                stack = max(1, int(st or item.get("stack_size", 64)))
            except ValueError:
                stack = item.get("stack_size", 64)
            category = choose_category(item.get("category", "uncategorized"))
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
            save_db()
            show_selected()

        def toggle_item():
            item = find_item(state.get("selected_id"))
            if not item:
                return
            item["enabled"] = not item.get("enabled", True)
            save_db()
            show_selected()

        def delete_item():
            item = find_item(state.get("selected_id"))
            if not item:
                return
            if not messagebox.askyesno("Удаление",
                                       f"Удалить «{item.get('name', '')}»?\nФото будут удалены.",
                                       parent=dialog):
                return
            for p in item.get("images", []):
                try:
                    fp = absolute_path(p)
                    if fp and os.path.isfile(fp):
                        os.remove(fp)
                except OSError:
                    pass
            self.reader.items_db["items"] = [x for x in get_items() if x.get("id") != item.get("id")]
            state["selected_id"] = None
            save_db()

        def add_photo():
            item = find_item(state.get("selected_id"))
            if not item:
                messagebox.showinfo("Фото", "Выберите предмет.", parent=dialog)
                return
            sel_paths = filedialog.askopenfilenames(
                parent=dialog, title="Добавить фото",
                filetypes=[("Изображения", "*.png *.jpg *.jpeg"), ("Все файлы", "*.*")])
            if not sel_paths:
                return
            images = item.get("images") or ([item.get("image")] if item.get("image") else [])
            for src in sel_paths:
                rel = next_image_path(item.get("name", "item"), ".png")
                copy_image_as_png(src, rel)
                images.append(rel)
            item["images"] = images
            item["image"] = images[0] if images else ""
            save_db()
            show_selected()
            self._log("✓ Фотографии добавлены.", self.GREEN)

        def remove_photo():
            item = find_item(state.get("selected_id"))
            sel = photo_list.curselection()
            if not item or not sel:
                return
            images = item.get("images") or []
            idx = sel[0]
            if idx >= len(images):
                return
            path = images[idx]
            if not messagebox.askyesno("Удалить фото",
                                       f"Удалить {os.path.basename(path)}?", parent=dialog):
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
            save_db()
            show_selected()

        def rename_photo():
            item = find_item(state.get("selected_id"))
            sel = photo_list.curselection()
            if not item or not sel:
                return
            images = item.get("images") or []
            path = images[sel[0]]
            cur = os.path.splitext(os.path.basename(path))[0]
            base = simpledialog.askstring("Переименовать фото", "Новое имя:",
                                          initialvalue=cur, parent=dialog)
            if base is None:
                return
            base = base.strip() or cur
            ext = os.path.splitext(path)[1] or ".png"
            nr = next_image_path(base, ext)
            try:
                shutil.move(absolute_path(path), absolute_path(nr))
                images[sel[0]] = nr
                item["images"] = images
                item["image"] = images[0] if images else ""
                save_db()
                show_selected()
            except OSError as e:
                messagebox.showerror("Фото", str(e), parent=dialog)

        def make_primary():
            item = find_item(state.get("selected_id"))
            sel = photo_list.curselection()
            if not item or not sel or sel[0] == 0:
                return
            images = item.get("images") or []
            idx = sel[0]
            if idx >= len(images):
                return
            photo = images.pop(idx)
            images.insert(0, photo)
            item["images"] = images
            item["image"] = images[0]
            save_db()
            show_selected()

        bottom = tk.Frame(dialog, bg=self.BG)
        bottom.pack(fill="x", padx=20, pady=(0, 18))
        self._button(bottom, "Добавить", add_item, True).pack(side="left", padx=3)
        self._button(bottom, "Изменить", edit_item).pack(side="left", padx=3)
        self._button(bottom, "Вкл/выкл", toggle_item).pack(side="left", padx=3)
        self._button(bottom, "Удалить", delete_item).pack(side="left", padx=3)
        self._button(bottom, "Доб. фото", add_photo).pack(side="left", padx=3)
        self._button(bottom, "Удал. фото", remove_photo).pack(side="left", padx=3)
        self._button(bottom, "Переим. фото", rename_photo).pack(side="left", padx=3)
        self._button(bottom, "Главное фото", make_primary).pack(side="left", padx=3)
        self._button(bottom, "Закрыть", dialog.destroy).pack(side="right", padx=3)
        refresh()

    # ── Профили ──
    def open_profile_manager(self):
        if self.busy:
            self._log("Нельзя менять профиль во время операции.", self.YELLOW)
            return
        dialog = self._modal("Профили", "700x500")
        tk.Label(dialog, text="Профили", bg=self.BG, fg=self.TEXT,
                 font=("Segoe UI", 19, "bold")).pack(anchor="w", padx=24, pady=(20, 4))
        tk.Label(dialog, text="У каждого профиля своя база, настройки и результаты.",
                 bg=self.BG, fg=self.MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=24)
        body = tk.Frame(dialog, bg=self.BG)
        body.pack(fill="both", expand=True, padx=24, pady=16)
        lb = tk.Listbox(body, bg=self.PANEL, fg=self.TEXT, selectbackground="#29304a",
                        relief="flat", highlightthickness=0, font=("Segoe UI", 11))
        lb.pack(fill="both", expand=True, side="left")
        bp = tk.Frame(body, bg=self.BG)
        bp.pack(side="right", fill="y", padx=(12, 0))
        profiles = load_profiles()
        selected_name = profiles.get("active", "Default")

        def refresh_profiles():
            lb.delete(0, "end")
            for e in profiles.get("profiles", []):
                m = "  ●" if e.get("name") == profiles.get("active") else ""
                lb.insert("end", e.get("name", "Profile") + m)
            for i, e in enumerate(profiles.get("profiles", [])):
                if e.get("name") == selected_name:
                    lb.selection_set(i)

        def chosen():
            sel = lb.curselection()
            return lb.get(sel[0]).replace("  ●", "") if sel else None

        def switch():
            nonlocal selected_name
            name = chosen()
            if not name:
                return
            if name == profiles.get("active"):
                dialog.destroy()
                return
            if not messagebox.askyesno("Профиль", f"Переключить на «{name}»?", parent=dialog):
                return
            if self.reader.auto_scan_active:
                self.reader.stop_auto_scan()
                self.auto_scan_btn.configure(text="▶ Автоскан", bg=self.PANEL_2)
            activate_profile(name)
            self.reader.reload_profile()
            self.reader.unknown_item_prompt = self._prompt_unknown_item
            self._refresh_profile_label()
            self._refresh_stats()
            self._log(f"✓ Профиль: {name}", self.GREEN)
            selected_name = name
            dialog.destroy()

        def create():
            name = simpledialog.askstring("Новый профиль", "Название:", parent=dialog)
            if not name or not name.strip():
                return
            name = name.strip()
            if any(c in name for c in '/\\:*?"<>|'):
                messagebox.showerror("Профиль", "Недопустимые символы.", parent=dialog)
                return
            if any(x.get("name") == name for x in profiles.get("profiles", [])):
                messagebox.showerror("Профиль", "Уже существует.", parent=dialog)
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
                messagebox.showerror("Профиль", str(e), parent=dialog)
                return
            profiles["profiles"] = load_profiles().get("profiles", [])
            refresh_profiles()
            self._log(f"✓ Создан профиль «{name}».", self.GREEN)

        def rename():
            name = chosen()
            if not name or name == "Default":
                messagebox.showinfo("Профиль", "Default нельзя переименовать.", parent=dialog)
                return
            nn = simpledialog.askstring("Переименование", "Новое имя:",
                                        initialvalue=name, parent=dialog)
            if not nn or not nn.strip():
                return
            nn = nn.strip()
            if any(c in nn for c in '/\\:*?"<>|'):
                messagebox.showerror("Профиль", "Недопустимые символы.", parent=dialog)
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
                messagebox.showerror("Профиль", str(e), parent=dialog)
                return
            entry["name"] = nn
            entry["directory"] = ndn.replace("\\", "/")
            pd["active"] = nn if pd.get("active") == name else pd.get("active")
            save_profiles(pd)
            profiles.clear()
            profiles.update(pd)
            refresh_profiles()

        def delete():
            name = chosen()
            if not name or name == "Default":
                messagebox.showinfo("Профиль", "Default нельзя удалить.", parent=dialog)
                return
            if name == load_profiles().get("active"):
                messagebox.showinfo("Профиль", "Сначала переключитесь.", parent=dialog)
                return
            if not messagebox.askyesno("Удаление", f"Удалить «{name}» и все данные?", parent=dialog):
                return
            entry = next((x for x in load_profiles().get("profiles", []) if x.get("name") == name), None)
            if entry:
                rel = entry.get("directory")
                if rel:
                    target = os.path.join(paths.BASE_DIR, rel)
                    if not os.path.abspath(target).startswith(os.path.abspath(paths.BASE_DIR) + os.sep):
                        messagebox.showerror("Профиль", "Отказ: неверный путь профиля.", parent=dialog)
                        return
                    try:
                        shutil.rmtree(target)
                    except OSError as e:
                        messagebox.showwarning("Профиль",
                                               f"Каталог не удалён: {e}\nУдалена только запись.", parent=dialog)
            profiles = load_profiles()
            profiles["profiles"] = [x for x in profiles.get("profiles", []) if x.get("name") != name]
            save_profiles(profiles)
            refresh_profiles()

        for text, cmd, accent in [("Переключить", switch, True), ("Новый", create, False),
                                 ("Переименовать", rename, False), ("Удалить", delete, False)]:
            self._button(bp, text, cmd, accent).pack(fill="x", pady=4)
        self._button(bp, "Закрыть", dialog.destroy).pack(fill="x", pady=(18, 4))
        refresh_profiles()
