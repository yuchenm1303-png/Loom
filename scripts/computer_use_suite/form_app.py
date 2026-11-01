"""A throwaway desktop form for live Computer Use runs. Writes what was submitted to argv[1]."""
import json
import sys
import tkinter as tk

out = sys.argv[1]
root = tk.Tk()
root.title("Loom Form Test")
root.geometry("520x420+200+150")
root.configure(padx=24, pady=18)
font = ("Segoe UI", 12)

fields = {}
for row, (key, label) in enumerate((("name", "Full name"), ("email", "Email"), ("city", "City"))):
    tk.Label(root, text=label, font=font).grid(row=row, column=0, sticky="w", pady=8)
    entry = tk.Entry(root, font=font, width=26)
    entry.grid(row=row, column=1, pady=8)
    fields[key] = entry

subscribe = tk.BooleanVar(value=False)
tk.Checkbutton(root, text="Subscribe to newsletter", variable=subscribe, font=font).grid(
    row=3, column=0, columnspan=2, sticky="w", pady=8)
settings = {"dark_mode": False}
status = tk.Label(root, text="", font=font, fg="#1a7f37")
status.grid(row=6, column=0, columnspan=2, pady=10)


def save(state):
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False)


def submit():
    save({"submitted": True, **{k: e.get() for k, e in fields.items()}, "subscribe": subscribe.get(), **settings})
    status.config(text="Submitted")


def open_settings():
    dialog = tk.Toplevel(root)
    dialog.title("Settings")
    dialog.geometry("320x180+300+260")
    dark = tk.BooleanVar(value=settings["dark_mode"])
    tk.Checkbutton(dialog, text="Dark mode", variable=dark, font=font).pack(pady=20)

    def ok():
        settings["dark_mode"] = dark.get()
        status.config(text="Settings saved")
        dialog.destroy()

    tk.Button(dialog, text="OK", width=10, font=font, command=ok).pack()
    dialog.transient(root)
    dialog.grab_set()


tk.Button(root, text="Settings...", width=12, font=font, command=open_settings).grid(row=4, column=0, pady=10, sticky="w")
tk.Button(root, text="Submit", width=12, font=font, command=submit).grid(row=4, column=1, pady=10, sticky="e")
save({"submitted": False})
root.lift()
root.attributes("-topmost", True)
root.after(400, lambda: root.attributes("-topmost", False))
root.focus_force()
root.mainloop()
