"""Dark theme for the native release review window."""

import tkinter as tk
from tkinter import ttk


BACKGROUND = "#111721"
PANEL = "#1a2330"
INPUT = "#131d29"
BORDER = "#344258"
TEXT = "#e7eef8"
MUTED = "#b4c3d7"
ACCENT = "#9cc9ff"


def apply_theme(root: tk.Tk) -> None:
    root.configure(background=BACKGROUND)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", font=("Segoe UI", 10), background=BACKGROUND, foreground=TEXT,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, troughcolor=INPUT,
                    focuscolor=ACCENT)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("Panel.TLabel", background=PANEL)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Title.TLabel", font=("Segoe UI", 24, "bold"))
    style.configure("Section.TLabel", font=("Segoe UI", 11, "bold"), background=PANEL)
    style.configure("TEntry", fieldbackground=INPUT, foreground=TEXT, insertcolor=TEXT, padding=6)
    style.configure("TCombobox", fieldbackground=INPUT, arrowcolor=TEXT, padding=5)
    style.map("TCombobox", fieldbackground=[("readonly", INPUT)], foreground=[("readonly", TEXT)])
    root.option_add("*TCombobox*Listbox.background", INPUT)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", "#28496c")
    style.configure("TButton", background="#233348", padding=(10, 6))
    style.map("TButton", background=[("active", "#304867")], foreground=[("disabled", "#8292a6")])
    style.configure("Apply.TButton", background=ACCENT, foreground=BACKGROUND, font=("Segoe UI", 10, "bold"))
    style.map("Apply.TButton", background=[("disabled", "#233348"), ("active", "#bddeff")],
              foreground=[("disabled", "#95a5bd"), ("!disabled", BACKGROUND)])
    style.configure("TCheckbutton", indicatorcolor=INPUT, padding=2)
    style.map("TCheckbutton", indicatorcolor=[("selected", ACCENT)])
    style.configure("TScrollbar", background="#344861", arrowcolor=MUTED, troughcolor=INPUT)
    style.configure("Treeview", background=INPUT, fieldbackground=INPUT, rowheight=26, borderwidth=1)
    style.configure("Treeview.Heading", background="#26364a", padding=(6, 5), font=("Segoe UI", 9, "bold"))
    style.map("Treeview", background=[("selected", "#28496c")], foreground=[("selected", "#ffffff")])
