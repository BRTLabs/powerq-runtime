from __future__ import annotations

import os
import subprocess
import tkinter as tk
from tkinter import messagebox, ttk

from .interpreter import CONFIG_PATH, evaluate_areas, load_config


class InterpreterEditor(tk.Toplevel):
    """Read-only configuration inspector; editing happens in VS Code."""

    def __init__(self, master):
        super().__init__(master)
        self.title("PLC Area Status Configuration")
        self.geometry("1000x680")
        self.minsize(760, 480)
        self.transient(master)

        top = ttk.Frame(self, padding=14)
        top.pack(fill="x")
        ttk.Label(top, text="AREA STATUS CONFIGURATION", font=("Segoe UI", 18, "bold")).pack(side="left")
        ttk.Label(top, text="Trusted Python rules edited by an administrator", foreground="#64716B").pack(side="left", padx=18)

        actions = ttk.Frame(self, padding=(14, 0))
        actions.pack(fill="x")
        ttk.Button(actions, text="Reload and validate", command=self.reload).pack(side="left")
        ttk.Button(actions, text="Open in VS Code", command=self.open_in_vscode).pack(side="left", padx=6)
        ttk.Button(actions, text="Open folder", command=self.open_folder).pack(side="left")

        ttk.Label(self, text=str(CONFIG_PATH), padding=(14, 10), foreground="#445").pack(fill="x")
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        self.text = tk.Text(body, wrap="none", font=("Consolas", 10), background="#17202A", foreground="#EAECEE")
        y = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        x = ttk.Scrollbar(body, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        y.grid(row=0, column=1, sticky="ns")
        x.grid(row=1, column=0, sticky="ew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status, padding=(14, 6)).pack(fill="x")
        self.reload()

    def reload(self):
        try:
            config = load_config(force=True)
            sample = evaluate_areas(config, {})
            lines = [
                f"Configuration: {config.path}",
                "",
                "SIGNAL ALIASES",
                "--------------",
            ]
            for alias, rule in config.signals.items():
                lines.append(f"{alias}: {rule.source[0]} / {rule.source[1]} ({len(rule.value_map)} mapped values)")
            lines += ["", "AREA RULES", "----------"]
            for area_id, rule in config.area_rules.items():
                lines.append(f"{area_id}: {rule.__name__} (empty-input probe={sample[area_id]})")
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            self.text.insert("1.0", "\n".join(lines))
            self.text.configure(state="disabled")
            self.status.set(f"Valid: {len(config.signals)} signal(s), {len(config.area_rules)} area(s)")
            return True
        except Exception as exc:
            self.status.set(f"Invalid: {exc}")
            messagebox.showerror("Area configuration", str(exc), parent=self)
            return False

    def open_in_vscode(self):
        try:
            subprocess.Popen(["code", str(CONFIG_PATH)])
        except Exception as exc:
            messagebox.showerror("VS Code", f"Could not open VS Code: {exc}", parent=self)

    def open_folder(self):
        try:
            os.startfile(CONFIG_PATH.parent)  # type: ignore[attr-defined]
        except AttributeError:
            subprocess.Popen(["xdg-open", str(CONFIG_PATH.parent)])
        except Exception as exc:
            messagebox.showerror("Configuration folder", str(exc), parent=self)


def open_interpreter(master):
    return InterpreterEditor(master)