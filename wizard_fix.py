import traceback
import tkinter as tk

import main

print("[WIZARD] wrapper started")

original_modal = main.ScannerGUI._modal


def safe_modal(self, title, geometry="620x420", minsize=None):
    print(f"[WIZARD] Creating modal: {title}")

    dialog = tk.Toplevel(self.root)
    dialog.title(title)
    dialog.geometry(geometry)

    if minsize:
        dialog.minsize(*minsize)

    dialog.configure(bg=self.BG)

    try:
        if self.root.winfo_exists() and self.root.winfo_viewable():
            dialog.transient(self.root)

        dialog.update_idletasks()
        dialog.deiconify()

        try:
            dialog.geometry("+250+250")
        except tk.TclError:
            pass

        dialog.lift()
        dialog.focus_force()

        try:
            dialog.attributes("-topmost", True)
            dialog.after(300, lambda: dialog.attributes("-topmost", False))
        except tk.TclError:
            pass

        dialog.grab_set()
    except tk.TclError as error:
        print(f"[WIZARD] modal warning: {error}")

    return dialog


main.ScannerGUI._modal = safe_modal

original_wizard = main.ScannerGUI._first_run_wizard


def safe_wizard(self):
    print("[WIZARD] _first_run_wizard started")

    try:
        original_wizard(self)
        print("[WIZARD] _first_run_wizard finished")
    except Exception:
        print("[WIZARD] _first_run_wizard error:")
        traceback.print_exc()

        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

        try:
            from tkinter import messagebox
            messagebox.showerror(
                "First run wizard error",
                "Мастер первого запуска упал.\n"
                "Подробности в консоли."
            )
        except Exception:
            pass


main.ScannerGUI._first_run_wizard = safe_wizard

original_init = main.ScannerGUI.__init__


def safe_init(self, reader):
    original_init(self, reader)

    try:
        profiles = main.load_profiles()
        if not profiles.get("setup_complete", False):
            print("[WIZARD] setup_complete=false -> showing main root as fallback")
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
    except Exception as error:
        print(f"[WIZARD] safe_init warning: {error}")


main.ScannerGUI.__init__ = safe_init


if __name__ == "__main__":
    print("[WIZARD] creating ChestReader")
    reader = main.ChestReader()

    print("[WIZARD] creating ScannerGUI")
    app = main.ScannerGUI(reader)

    print("[WIZARD] entering mainloop")
    app.run()