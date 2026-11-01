"""Throwaway desktop apps for the live Computer Use suite. usage: suite_app.py MODE RESULT_JSON [ARG]

Every app writes what the user (the agent) did to RESULT_JSON, so a run is checked from facts, not from
the agent's own report.
"""
import json
import sys
import tkinter as tk
from tkinter import filedialog

mode, out = sys.argv[1], sys.argv[2]
arg = sys.argv[3] if len(sys.argv) > 3 else ""
font = ("Segoe UI", 11)
root = tk.Tk()


def save(**state):
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False)


def place(title, geometry):
    root.title(title)
    root.geometry(geometry)
    root.configure(padx=16, pady=14)


if mode == "export":
    place("Report Exporter", "460x220+260+180")
    tk.Label(root, text="Quarterly report is ready to export.", font=font).pack(pady=18)

    def export():
        path = filedialog.asksaveasfilename(parent=root, title="Export report", defaultextension=".txt",
                                            filetypes=[("Text", "*.txt")], initialfile="report.txt")
        if path:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("Q3 report\n")
            save(exported_to=path.replace("/", "\\"))
            status.config(text="Exported")

    tk.Button(root, text="Export...", font=font, width=12, command=export).pack()
    status = tk.Label(root, text="", font=font, fg="#1a7f37")
    status.pack(pady=10)
    save(exported_to="")

elif mode == "list":
    place("Pick a Country", "360x330+300+160")
    countries = sorted("""Argentina Australia Austria Belgium Brazil Bulgaria Canada Chile China Colombia Croatia Cyprus
    Czechia Denmark Egypt Estonia Finland France Germany Greece Hungary Iceland India Indonesia Ireland Israel Italy
    Japan Kenya Latvia Lithuania Luxembourg Malaysia Malta Mexico Morocco Netherlands Nigeria Norway Pakistan Peru
    Philippines Poland Portugal Romania Serbia Singapore Slovakia Slovenia Spain Sweden Switzerland Thailand Turkey
    Ukraine Uruguay Vietnam Zambia""".split())
    frame = tk.Frame(root)
    frame.pack(fill="both", expand=True)
    box = tk.Listbox(frame, font=font, height=9, exportselection=False)
    bar = tk.Scrollbar(frame, command=box.yview)
    box.configure(yscrollcommand=bar.set)
    box.pack(side="left", fill="both", expand=True)
    bar.pack(side="right", fill="y")
    for name in countries:
        box.insert("end", name)

    def choose():
        picked = box.get(box.curselection()[0]) if box.curselection() else ""
        save(chosen=picked)
        status.config(text=f"Chosen: {picked}")

    tk.Button(root, text="Choose", font=font, width=10, command=choose).pack(pady=8)
    status = tk.Label(root, text="", font=font)
    status.pack()
    save(chosen="")

elif mode == "seat":
    place("Seat Picker", "560x400+240+140")
    picked = {"seat": ""}
    grid = tk.Frame(root)
    grid.pack()
    small = ("Segoe UI", 8)
    for r, row in enumerate("ABCDEFGH"):
        for c in range(1, 11):
            label = f"{row}{c}"
            tk.Button(grid, text=label, font=small, width=3, padx=0, pady=0,
                      command=lambda value=label: (picked.update(seat=value), shown.config(text=f"Selected {value}"))
                      ).grid(row=r, column=c, padx=1, pady=1)
    shown = tk.Label(root, text="No seat selected", font=font)
    shown.pack(pady=8)

    def confirm():
        save(confirmed=picked["seat"])
        shown.config(text=f"Confirmed {picked['seat']}")

    tk.Button(root, text="Confirm", font=font, width=10, command=confirm).pack()
    save(confirmed="")

elif mode == "source":
    place("Verification Code", "320x160+80+520")
    tk.Label(root, text="Your code is", font=font).pack(pady=6)
    tk.Label(root, text=arg, font=("Consolas", 20, "bold")).pack()

elif mode == "target":
    place("Enter Code", "380x200+700+200")
    tk.Label(root, text="Verification code", font=font).pack(pady=8)
    entry = tk.Entry(root, font=font, width=16)
    entry.pack()

    def submit():
        save(code=entry.get().strip())
        status.config(text="Submitted")

    tk.Button(root, text="Submit", font=font, width=10, command=submit).pack(pady=10)
    status = tk.Label(root, text="", font=font, fg="#1a7f37")
    status.pack()
    save(code="")

elif mode == "rename":
    place("Project Settings", "460x220+260+180")
    tk.Label(root, text="Project name", font=font).pack(pady=8)
    entry = tk.Entry(root, font=font, width=30)
    entry.insert(0, "Untitled project (draft copy 3)")
    entry.pack()

    def apply():
        save(name=entry.get())
        status.config(text="Saved")

    tk.Button(root, text="Save", font=font, width=10, command=apply).pack(pady=10)
    status = tk.Label(root, text="", font=font, fg="#1a7f37")
    status.pack()
    save(name="Untitled project (draft copy 3)")

else:
    raise SystemExit(f"unknown mode {mode}")

root.lift()
root.attributes("-topmost", True)
root.after(400, lambda: root.attributes("-topmost", False))
root.focus_force()
root.mainloop()
