"""A large plain window that opens on top of the test form, like an unrelated app."""
import tkinter as tk

root = tk.Tk()
root.title("Unrelated Notes")
root.geometry("1100x760+120+80")
tk.Label(root, text="Some other application", font=("Segoe UI", 20)).pack(pady=40)
root.lift()
root.focus_force()
root.mainloop()
