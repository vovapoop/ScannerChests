"""GUI mixin: переиспользуемые виджеты и хелперы."""
import os
import shutil
import tkinter as tk
from tkinter import filedialog

from chest_scanner.commons import absolute_path
from chest_scanner.items import sanitize_filename
from chest_scanner import paths


class GuiWidgetsMixin:
    def _button(self, parent, text, command, accent=False):
        bg = self.ACCENT if accent else self.PANEL_2
        act = self.ACCENT_HOVER if accent else "#252c36"
        b = tk.Button(parent, text=text, command=command, bg=bg, fg=self.TEXT,
                      activebackground=act, activeforeground=self.TEXT,
                      relief="flat", bd=0, padx=16, pady=9, cursor="hand2",
                      font=("Segoe UI", 10, "bold"))
        b.bind("<Enter>", lambda e: b.configure(bg=act))
        b.bind("<Leave>", lambda e: b.configure(bg=bg))
        return b

    def _card(self, parent, title, value, color=None):
        f = tk.Frame(parent, bg=self.PANEL, highlightbackground=self.BORDER, highlightthickness=1)
        tk.Label(f, text=title.upper(), bg=self.PANEL, fg=self.MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=16, pady=(12, 2))
        lb = tk.Label(f, text=value, bg=self.PANEL, fg=color or self.TEXT,
                      font=("Segoe UI", 21, "bold"))
        lb.pack(anchor="w", padx=16, pady=(0, 12))
        return f, lb

    def _settings_card(self, parent, title, subtitle=None):
        card = tk.Frame(parent, bg=self.PANEL, highlightbackground=self.BORDER, highlightthickness=1)
        card.pack(fill="x", padx=16, pady=(12, 0))
        tk.Label(card, text=title, bg=self.PANEL, fg=self.TEXT,
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=16, pady=(12, 2))
        if subtitle:
            tk.Label(card, text=subtitle, bg=self.PANEL, fg=self.MUTED,
                     font=("Segoe UI", 9), wraplength=680, justify="left").pack(anchor="w", padx=16)
        inner = tk.Frame(card, bg=self.PANEL)
        inner.pack(fill="x", padx=16, pady=(10, 14))
        return inner

    def _settings_entry(self, parent, label, var, width=18):
        row = tk.Frame(parent, bg=self.PANEL)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, bg=self.PANEL, fg=self.MUTED,
                 font=("Segoe UI", 10)).pack(side="left")
        e = tk.Entry(row, textvariable=var, bg=self.PANEL_2, fg=self.TEXT,
                     insertbackground=self.TEXT, relief="flat", width=width,
                     justify="right", font=("Segoe UI", 10))
        e.pack(side="right", ipady=5)
        return e

    def _settings_check(self, parent, label, var):
        tk.Checkbutton(parent, text=label, variable=var, bg=self.PANEL, fg=self.TEXT,
                       selectcolor=self.PANEL_2, activebackground=self.PANEL,
                       activeforeground=self.TEXT, font=("Segoe UI", 10),
                       anchor="w").pack(fill="x", pady=4)

    def _settings_scale(self, parent, label, var, from_, to, fmt="{:.2f}"):
        frame = tk.Frame(parent, bg=self.PANEL)
        frame.pack(fill="x", pady=(6, 2))
        top = tk.Frame(frame, bg=self.PANEL)
        top.pack(fill="x")
        tk.Label(top, text=label, bg=self.PANEL, fg=self.MUTED,
                 font=("Segoe UI", 10)).pack(side="left")
        vl = tk.Label(top, text=fmt.format(var.get()), bg=self.PANEL, fg=self.TEXT,
                      font=("Segoe UI", 10, "bold"))
        vl.pack(side="right")

        def cmd(v):
            try:
                vl.configure(text=fmt.format(float(v)))
            except Exception:
                pass

        tk.Scale(frame, from_=from_, to=to, orient="horizontal", variable=var,
                 command=cmd, bg=self.PANEL, troughcolor=self.PANEL_2, fg=self.TEXT,
                 highlightthickness=0, bd=0, length=480, sliderlength=18,
                 activebackground=self.ACCENT, showvalue=0).pack(fill="x", pady=(2, 6))

    def _template_selector(self, parent, var, title):
        row = tk.Frame(parent, bg=self.PANEL)
        row.pack(fill="x")
        preview = tk.Label(row, text="Нет файла", bg=self.PANEL_2, fg=self.MUTED, width=14, height=6)
        preview.pack(side="left", padx=(0, 14))
        info = tk.Frame(row, bg=self.PANEL)
        info.pack(side="left", fill="both", expand=True)
        tk.Label(info, textvariable=var, bg=self.PANEL, fg=self.TEXT,
                 wraplength=520, justify="left", font=("Segoe UI", 9)).pack(anchor="w")

        def choose():
            path = filedialog.askopenfilename(
                parent=self._settings_win, title=title,
                filetypes=[("Изображения", "*.png *.jpg *.jpeg"), ("Все файлы", "*.*")])
            if not path:
                return
            rel = self._import_template_file(path, os.path.basename(path))
            var.set(rel)
            self._update_template_preview(preview, rel)

        tk.Button(info, text="Выбрать файл...", command=choose, bg=self.ACCENT, fg="white",
                  activebackground=self.ACCENT_HOVER, relief="flat", bd=0, padx=12, pady=6,
                  cursor="hand2", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(8, 0))
        self._update_template_preview(preview, var.get())

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

    def _update_template_preview(self, label, path):
        label.configure(image="", text="Нет файла")
        full = absolute_path(path)
        if not full or not os.path.isfile(full):
            return
        try:
            from PIL import Image, ImageTk
            img = Image.open(full)
            img.thumbnail((84, 84))
            photo = ImageTk.PhotoImage(img)
            label.configure(image=photo, text="")
            label.image = photo
        except Exception:
            try:
                photo = tk.PhotoImage(file=full)
                sc = max(1, max(photo.width(), photo.height()) // 84)
                if sc > 1:
                    photo = photo.subsample(sc, sc)
                label.configure(image=photo, text="")
                label.image = photo
            except Exception:
                label.configure(image="", text="Нет предпросмотра")

    def _parse_int(self, var, name):
        try:
            return int(float(str(var.get()).strip()))
        except Exception:
            raise ValueError(f"{name}: нужно целое число.")

    def _parse_float(self, var, name):
        try:
            return float(str(var.get()).strip())
        except Exception:
            raise ValueError(f"{name}: нужно число.")
