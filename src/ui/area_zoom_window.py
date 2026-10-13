"""Standalone, pure-Tk area signal status window."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

STATE_NAMES = {0: "Fault", 1: "Idle", 2: "Running"}
STATE_COLORS = {0: "#D9342B", 1: "#7A7A7A", 2: "#00B56A"}


class AreaZoomWindow:
    """Resizable Toplevel showing one area's overall and signal states.

    Only tkinter widgets are used. Closing is intentionally delegated to the
    window manager's title-bar close button.
    """

    def __init__(self, parent: tk.Misc, area_id: str, rows_provider: Callable[[], tuple[int | None, list[tuple[str, str, int | None, object]]]], on_close: Callable[["AreaZoomWindow"], None] | None = None) -> None:
        self.area_id = area_id
        self.rows_provider = rows_provider
        self.on_close = on_close
        self.window = tk.Toplevel(parent)
        display_name = area_id.replace("_", " ").title()
        self.window.title(f"Zoom view {display_name}")
        self.window.minsize(620, 360)
        self.window.resizable(True, True)
        self.window.configure(bg="#F2F2F2")
        self.window.protocol("WM_DELETE_WINDOW", self._close)

        width = min(max(760, parent.winfo_width() * 3 // 4), self.window.winfo_screenwidth() - 80)
        height = min(max(480, parent.winfo_height() * 3 // 4), self.window.winfo_screenheight() - 100)
        x = max(0, (self.window.winfo_screenwidth() - width) // 2)
        y = max(0, (self.window.winfo_screenheight() - height) // 2)
        self.window.geometry(f"{width}x{height}+{x}+{y}")

        self.window.grid_columnconfigure(0, weight=1)
        self.window.grid_rowconfigure(2, weight=1)
        tk.Label(self.window, text=display_name, bg="#F2F2F2", fg="#111111", font=("Segoe UI", 22, "bold"), anchor="w", padx=20, pady=16).grid(row=0, column=0, sticky="ew")
        self.status_tile = tk.Label(self.window, bg="#8A8A8A", fg="white", font=("Segoe UI", 20, "bold"), pady=18)
        self.status_tile.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 16))

        table_shell = tk.Frame(self.window, bg="white", highlightbackground="#B8B8B8", highlightthickness=1)
        table_shell.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 20))
        table_shell.grid_rowconfigure(1, weight=1)
        table_shell.grid_columnconfigure(0, weight=1)
        self.header = tk.Frame(table_shell, bg="#E9ECEF")
        self.header.grid(row=0, column=0, sticky="ew")
        self.canvas = tk.Canvas(table_shell, bg="white", highlightthickness=0)
        self.scrollbar = tk.Scrollbar(table_shell, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.scrollbar.grid(row=1, column=1, sticky="ns")
        self.body = tk.Frame(self.canvas, bg="white")
        self.body_window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._sync_scrollregion)
        self.canvas.bind("<Configure>", self._fit_body_width)
        self.refresh()

    def _sync_scrollregion(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _fit_body_width(self, event) -> None:
        self.canvas.itemconfigure(self.body_window, width=event.width)

    @staticmethod
    def _column_weights(rows: list[tuple[str, str, int | None, object]]) -> tuple[int, int, int, int]:
        samples = [("Signal", "PLC", "Status", "Raw value"), *[(a, b, STATE_NAMES.get(c, "Unknown"), "" if d is None else str(d)) for a, b, c, d in rows]]
        lengths = [max(len(str(row[index])) for row in samples) for index in range(4)]
        return tuple(max(1, min(4, (length + 7) // 8)) for length in lengths)

    def refresh(self) -> None:
        if not self.window.winfo_exists():
            return
        overall_state, rows = self.rows_provider()
        self.status_tile.configure(text=f"OVERALL STATUS   {STATE_NAMES.get(overall_state, 'No data')}", bg=STATE_COLORS.get(overall_state, "#8A8A8A"))
        for parent in (self.header, self.body):
            for child in parent.winfo_children():
                child.destroy()
        weights = self._column_weights(rows)
        for column, weight in enumerate(weights):
            self.header.grid_columnconfigure(column, weight=weight, uniform="zoom_columns")
            self.body.grid_columnconfigure(column, weight=weight, uniform="zoom_columns")
        for column, text in enumerate(("Signal", "PLC", "Status", "Raw value")):
            tk.Label(self.header, text=text, bg="#E9ECEF", fg="#111111", font=("Segoe UI", 11, "bold"), padx=10, pady=9, relief="solid", bd=1).grid(row=0, column=column, sticky="nsew")
        if not rows:
            tk.Label(self.body, text="No signals are configured for this area.\nAdd signal DB addresses in PLC Control to display live equipment statuses.", bg="white", fg="#444444", font=("Segoe UI", 13), justify="center", pady=40).grid(row=0, column=0, columnspan=4, sticky="nsew")
        else:
            for row_index, (signal, plc, state, raw) in enumerate(rows):
                values = (signal, plc, STATE_NAMES.get(state, "Unknown"), "" if raw is None else str(raw))
                row_bg = "#FFFFFF" if row_index % 2 == 0 else "#F6F7F8"
                for column, value in enumerate(values):
                    foreground = STATE_COLORS.get(state, "#333333") if column == 2 else "#111111"
                    tk.Label(self.body, text=value, bg=row_bg, fg=foreground, font=("Segoe UI", 11), padx=10, pady=8, anchor="w" if column == 0 else "center", relief="solid", bd=1).grid(row=row_index, column=column, sticky="nsew")
        self.window.after_idle(self._sync_scrollregion)

    def _close(self) -> None:
        if self.on_close is not None:
            self.on_close(self)
        self.window.destroy()