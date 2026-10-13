"""Custom image-matrix view dialog for the Power Queue Manager UI."""

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable


class ChangeViewOptions:
    """Own the Change View window and validate custom row/column values."""

    def __init__(
        self,
        parent: tk.Misc,
        current_rows: int,
        current_columns: int,
        on_apply: Callable[[int, int], None],
        on_reset_zoom: Callable[[], None] | None = None,
    ) -> None:
        self.parent = parent
        self.on_apply = on_apply
        self.on_reset_zoom = on_reset_zoom

        self.window = tk.Toplevel(parent)
        self.window.title("Change Matrix View")
        self.window.resizable(False, False)
        self.window.transient(parent)
        self.window.grab_set()
        self.window.columnconfigure(1, weight=1)

        self.rows_var = tk.StringVar(value=str(current_rows))
        self.columns_var = tk.StringVar(value=str(current_columns))

        ttk.Label(
            self.window,
            text="Choose how the images are arranged:",
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, columnspan=2, padx=14, pady=(14, 10), sticky="w")

        ttk.Label(self.window, text="Rows:").grid(
            row=1, column=0, padx=(14, 8), pady=5, sticky="w"
        )
        self.rows_spinbox = ttk.Spinbox(
            self.window,
            from_=1,
            to=50,
            textvariable=self.rows_var,
            width=10,
        )
        self.rows_spinbox.grid(row=1, column=1, padx=(0, 14), pady=5, sticky="ew")

        ttk.Label(self.window, text="Columns:").grid(
            row=2, column=0, padx=(14, 8), pady=5, sticky="w"
        )
        self.columns_spinbox = ttk.Spinbox(
            self.window,
            from_=1,
            to=50,
            textvariable=self.columns_var,
            width=10,
        )
        self.columns_spinbox.grid(row=2, column=1, padx=(0, 14), pady=5, sticky="ew")

        ttk.Label(
            self.window,
            text="Examples: 1 row x 6 columns, 2 rows x 3 columns, or 4 rows x 2 columns.",
            foreground="gray45",
            wraplength=330,
        ).grid(row=3, column=0, columnspan=2, padx=14, pady=(6, 10), sticky="w")

        button_frame = ttk.Frame(self.window)
        button_frame.grid(row=4, column=0, columnspan=2, padx=14, pady=(0, 14), sticky="ew")
        button_frame.columnconfigure((0, 1), weight=1)

        ttk.Button(button_frame, text="Apply", command=self._apply).grid(
            row=0, column=0, padx=(0, 5), sticky="ew"
        )
        ttk.Button(button_frame, text="Cancel", command=self.window.destroy).grid(
            row=0, column=1, padx=(5, 0), sticky="ew"
        )

        if self.on_reset_zoom is not None:
            ttk.Button(
                self.window,
                text="Reset Zoom",
                command=self.on_reset_zoom,
            ).grid(row=5, column=0, columnspan=2, padx=14, pady=(0, 14), sticky="ew")

        self.window.bind("<Return>", lambda _event: self._apply())
        self.window.bind("<Escape>", lambda _event: self.window.destroy())
        self.rows_spinbox.focus_set()
        self.rows_spinbox.selection_range(0, tk.END)
        self._center_on_parent()

    def _apply(self) -> None:
        """Validate input, apply the custom layout, and close the window."""
        try:
            rows = int(self.rows_var.get().strip())
            columns = int(self.columns_var.get().strip())
        except ValueError:
            messagebox.showwarning(
                "Invalid matrix view",
                "Rows and columns must be whole numbers.",
                parent=self.window,
            )
            return

        if not 1 <= rows <= 50 or not 1 <= columns <= 50:
            messagebox.showwarning(
                "Invalid matrix view",
                "Rows and columns must be between 1 and 50.",
                parent=self.window,
            )
            return

        self.on_apply(rows, columns)
        self.window.destroy()

    def _center_on_parent(self) -> None:
        self.window.update_idletasks()
        width = self.window.winfo_reqwidth()
        height = self.window.winfo_reqheight()
        parent_x = self.parent.winfo_rootx()
        parent_y = self.parent.winfo_rooty()
        parent_width = self.parent.winfo_width()
        parent_height = self.parent.winfo_height()
        x = parent_x + max(0, (parent_width - width) // 2)
        y = parent_y + max(0, (parent_height - height) // 2)
        self.window.geometry(f"{width}x{height}+{x}+{y}")