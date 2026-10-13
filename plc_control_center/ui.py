"""Pure-tkinter PLC acquisition, configuration, and live-data window."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog

from .config import TYPES, load, save, validate
from .discovery import local_network, scan
from .engine import Engine
from .interpreter_ui import open_interpreter
from .models import Area, Plc, Signal
from .storage import rows_to_csv, rows_to_xlsx

ACCENT = "#00AB5F"
DARK = "#007A43"
DANGER = "#D9342B"
BG = "#F3F5F4"
CARD = "#FFFFFF"
TEXT = "#24312B"
MUTED = "#64716B"
BORDER = "#D7DEDA"
HEADER = "#E8ECEA"
SELECT = "#DDF4EA"
FONT = ("Segoe UI", 10)


def make_button(parent, text, command, *, role="normal"):
    colors = {
        "normal": ("#E8ECEA", TEXT, "#D8DEDB"),
        "accent": (ACCENT, "white", DARK),
        "danger": (DANGER, "white", "#B52B24"),
    }
    bg, fg, active = colors[role]
    return tk.Button(
        parent, text=text, command=command, font=FONT, bg=bg, fg=fg,
        activebackground=active, activeforeground=fg, relief="flat", bd=0,
        padx=14, pady=8, cursor="hand2", highlightthickness=0,
    )


def make_entry(parent, variable):
    return tk.Entry(
        parent, textvariable=variable, font=FONT, relief="solid", bd=1,
        highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
    )


class CanvasTable(tk.Frame):
    """Resizable, scrollable, selectable table built only with tkinter Canvas."""

    def __init__(self, parent, columns, weights, *, row_height=34, empty_text="No rows"):
        super().__init__(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        self.columns = tuple(columns)
        self.weights = tuple(weights)
        self.row_height = row_height
        self.empty_text = empty_text
        self.rows = []
        self.selected_key = None
        self.on_select = None
        self.on_double_click = None

        self.canvas = tk.Canvas(self, bg="white", bd=0, highlightthickness=0)
        self.scrollbar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.bind("<Configure>", lambda _event: self._draw())
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Double-1>", self._double_click)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", lambda _e: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind("<Button-5>", lambda _e: self.canvas.yview_scroll(1, "units"))

    def set_rows(self, rows, *, preserve_selection=True):
        old = self.selected_key if preserve_selection else None
        self.rows = list(rows)
        keys = {row[0] for row in self.rows}
        self.selected_key = old if old in keys else None
        self._draw()

    def select(self, key):
        self.selected_key = key
        self._draw()

    def _bounds(self):
        width = max(1, self.canvas.winfo_width())
        total = sum(self.weights)
        raw = [width * weight / total for weight in self.weights]
        bounds = [0]
        for value in raw[:-1]:
            bounds.append(bounds[-1] + value)
        bounds.append(width)
        return bounds

    def _draw(self):
        if not self.canvas.winfo_exists():
            return
        self.canvas.delete("all")
        bounds = self._bounds()
        width = bounds[-1]
        header_height = 36
        self.canvas.create_rectangle(0, 0, width, header_height, fill=HEADER, outline=BORDER)
        for index, title in enumerate(self.columns):
            anchor = "w" if index == 0 else "center"
            x = bounds[index] + 11 if anchor == "w" else (bounds[index] + bounds[index + 1]) / 2
            self.canvas.create_text(x, header_height / 2, text=title, anchor=anchor, fill=TEXT,
                                    font=("Segoe UI", 10, "bold"))
            if index:
                self.canvas.create_line(bounds[index], 0, bounds[index], header_height, fill=BORDER)

        if not self.rows:
            height = max(self.canvas.winfo_height(), header_height + 90)
            self.canvas.create_text(width / 2, header_height + 45, text=self.empty_text, fill=MUTED,
                                    font=FONT, justify="center")
            self.canvas.configure(scrollregion=(0, 0, width, height))
            return

        for row_index, (key, values) in enumerate(self.rows):
            top = header_height + row_index * self.row_height
            bottom = top + self.row_height
            fill = SELECT if key == self.selected_key else ("white" if row_index % 2 == 0 else "#F7F9F8")
            self.canvas.create_rectangle(0, top, width, bottom, fill=fill, outline=BORDER, tags=(f"row:{row_index}",))
            for col, value in enumerate(values):
                anchor = "w" if col == 0 else "center"
                x = bounds[col] + 11 if anchor == "w" else (bounds[col] + bounds[col + 1]) / 2
                text = "" if value is None else str(value)
                self.canvas.create_text(x, (top + bottom) / 2, text=text, anchor=anchor,
                                        fill=TEXT, font=FONT, tags=(f"row:{row_index}",))
                if col:
                    self.canvas.create_line(bounds[col], top, bounds[col], bottom, fill=BORDER)
        content_height = header_height + len(self.rows) * self.row_height
        self.canvas.configure(scrollregion=(0, 0, width, max(content_height, self.canvas.winfo_height())))

    def _row_at(self, event):
        y = self.canvas.canvasy(event.y) - 36
        index = int(y // self.row_height)
        return index if 0 <= index < len(self.rows) else None

    def _click(self, event):
        index = self._row_at(event)
        if index is None:
            return
        self.selected_key = self.rows[index][0]
        self._draw()
        if self.on_select:
            self.on_select(self.selected_key)

    def _double_click(self, event):
        index = self._row_at(event)
        if index is not None and self.on_double_click:
            self.on_double_click(self.rows[index][0])

    def _wheel(self, event):
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")


class Form(tk.Toplevel):
    def __init__(self, parent, title, fields, values):
        super().__init__(parent)
        self.title(title)
        self.result = None
        self.configure(bg=CARD)
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)
        self.vars = []
        body = tk.Frame(self, bg=CARD, padx=22, pady=20)
        body.pack(fill="both", expand=True)
        first = None
        for row, ((caption, kind, choices), value) in enumerate(zip(fields, values)):
            tk.Label(body, text=caption, font=FONT, bg=CARD, fg=TEXT).grid(row=row, column=0, sticky="w", pady=6)
            variable = tk.StringVar(value=str(value))
            self.vars.append(variable)
            if kind == "combo":
                widget = tk.OptionMenu(body, variable, *choices)
                widget.configure(font=FONT, bg="white", activebackground=SELECT, relief="solid", bd=1,
                                 highlightthickness=0, width=27, anchor="w")
                widget["menu"].configure(font=FONT)
            else:
                widget = make_entry(body, variable)
            widget.grid(row=row, column=1, sticky="ew", padx=(14, 0), pady=6)
            first = first or widget
        actions = tk.Frame(body, bg=CARD)
        actions.grid(row=len(fields), column=0, columnspan=2, sticky="e", pady=(16, 0))
        make_button(actions, "Cancel", self.destroy).pack(side="left", padx=(0, 8))
        make_button(actions, "Save", self._done, role="accent").pack(side="left")
        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Return>", lambda _e: self._done())
        self.update_idletasks()
        x = parent.winfo_rootx() + max(20, (parent.winfo_width() - self.winfo_reqwidth()) // 2)
        y = parent.winfo_rooty() + max(20, (parent.winfo_height() - self.winfo_reqheight()) // 2)
        self.geometry(f"+{x}+{y}")
        if first:
            first.focus_set()

    def _done(self):
        self.result = [variable.get().strip() for variable in self.vars]
        self.destroy()


class App(tk.Frame):
    """PLC Control Center window. The acquisition and persistence layers are unchanged."""

    def __init__(self, master):
        super().__init__(master, bg=BG)
        self.root = master
        self.project = load()
        self.events = queue.Queue()
        self.engine = Engine(self.events)
        self.area_index = 0
        self.plc_index = None
        self.current_tab = "configuration"
        self._closing = False

        master.title("PLC Control Center")
        master.geometry("1380x840")
        master.minsize(880, 600)
        master.configure(bg=BG)
        master.protocol("WM_DELETE_WINDOW", self.close)
        self.pack(fill="both", expand=True)
        self._build()
        self.refresh_structure()
        master.after(100, self.process_events)

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = tk.Frame(self, bg=BG, padx=20, pady=16)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)
        tk.Label(header, text="PLC CONTROL CENTER", bg=BG, fg=TEXT,
                 font=("Segoe UI", 22, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(header, text="Read-only acquisition and export", bg=BG, fg=MUTED,
                 font=FONT).grid(row=0, column=1, sticky="w", padx=(24, 12))
        actions = tk.Frame(header, bg=BG)
        actions.grid(row=0, column=2, sticky="e")
        make_button(actions, "Interpreter", lambda: open_interpreter(self.root)).pack(side="left", padx=(0, 8))
        make_button(actions, "Save", self.save_project).pack(side="left", padx=(0, 8))
        make_button(actions, "Stop", self.stop, role="danger").pack(side="left", padx=(0, 8))
        make_button(actions, "Start engine", self.start, role="accent").pack(side="left")

        self.panes = tk.PanedWindow(self, orient="horizontal", bg=BG, sashwidth=7, sashrelief="flat", bd=0)
        self.panes.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 12))
        self.left = tk.Frame(self.panes, bg=CARD, padx=14, pady=14, highlightthickness=1, highlightbackground=BORDER)
        self.right = tk.Frame(self.panes, bg=CARD, padx=14, pady=14, highlightthickness=1, highlightbackground=BORDER)
        self.panes.add(self.left, minsize=300, width=390)
        self.panes.add(self.right, minsize=520)
        self._build_structure()
        self._build_workspace()

        self.status = tk.StringVar(value="Ready")
        tk.Label(self, textvariable=self.status, bg=ACCENT, fg="white", font=FONT,
                 anchor="w", padx=12, pady=8).grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 14))

    def _build_structure(self):
        self.left.grid_columnconfigure(0, weight=1)
        self.left.grid_rowconfigure(1, weight=1)
        tk.Label(self.left, text="PLANT STRUCTURE", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 12, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 10))
        self.structure = CanvasTable(self.left, ("Name", "Type", "Status"), (5, 2, 3), empty_text="No areas configured")
        self.structure.grid(row=1, column=0, sticky="nsew")
        self.structure.on_select = self._structure_selected
        self.structure.on_double_click = lambda _key: self.edit_structure()

        controls = tk.Frame(self.left, bg=CARD)
        controls.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        for text, command in (("Area +", self.add_area), ("PLC +", self.add_plc), ("Edit", self.edit_structure), ("Remove", self.remove_structure)):
            make_button(controls, text, command).pack(side="left", padx=(0, 6))

        scan_bar = tk.Frame(self.left, bg=CARD)
        scan_bar.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        scan_bar.grid_columnconfigure(0, weight=1)
        self.network = tk.StringVar(value=str(local_network()))
        make_entry(scan_bar, self.network).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        make_button(scan_bar, "Scan PLCs", self.scan).grid(row=0, column=1)

    def _build_workspace(self):
        self.right.grid_columnconfigure(0, weight=1)
        self.right.grid_rowconfigure(1, weight=1)
        tabs = tk.Frame(self.right, bg=CARD)
        tabs.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.config_button = make_button(tabs, "Configuration", lambda: self.show_tab("configuration"), role="accent")
        self.live_button = make_button(tabs, "Live data", lambda: self.show_tab("live"))
        self.config_button.pack(side="left")
        self.live_button.pack(side="left", padx=(8, 0))

        self.page_host = tk.Frame(self.right, bg=CARD)
        self.page_host.grid(row=1, column=0, sticky="nsew")
        self.page_host.grid_columnconfigure(0, weight=1)
        self.page_host.grid_rowconfigure(0, weight=1)
        self.config_page = tk.Frame(self.page_host, bg=CARD)
        self.live_page = tk.Frame(self.page_host, bg=CARD)
        self._build_config_page()
        self._build_live_page()
        self.show_tab("configuration")

    def _build_config_page(self):
        page = self.config_page
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=3)
        page.grid_rowconfigure(3, weight=2)

        signal_bar = tk.Frame(page, bg=CARD)
        signal_bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        signal_bar.grid_columnconfigure(0, weight=1)
        tk.Label(signal_bar, text="SIGNALS", bg=CARD, fg=TEXT, font=("Segoe UI", 12, "bold")).grid(row=0, column=0, sticky="w")
        make_button(signal_bar, "Edit", self.edit_signal).grid(row=0, column=1, padx=(0, 8))
        make_button(signal_bar, "Remove", self.remove_signal).grid(row=0, column=2, padx=(0, 8))
        make_button(signal_bar, "Add signal", self.add_signal, role="accent").grid(row=0, column=3)

        self.signals = CanvasTable(page, ("Signal", "Address", "Type", "Unit", "Area state"), (5, 3, 2, 2, 2),
                                   empty_text="Select a PLC to view signals")
        self.signals.grid(row=1, column=0, sticky="nsew")
        self.signals.on_double_click = lambda _key: self.edit_signal()

        separator = tk.Frame(page, bg=BORDER, height=1)
        separator.grid(row=2, column=0, sticky="ew", pady=14)

        destination = tk.Frame(page, bg=CARD)
        destination.grid(row=3, column=0, sticky="new")
        destination.grid_columnconfigure(1, weight=1)
        tk.Label(destination, text="DATA DESTINATION", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 12, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))
        self.folder = tk.StringVar()
        self.dataset = tk.StringVar()
        tk.Label(destination, text="Folder", bg=CARD, fg=TEXT, font=FONT).grid(row=1, column=0, sticky="w", pady=5)
        make_entry(destination, self.folder).grid(row=1, column=1, sticky="ew", padx=10, pady=5)
        make_button(destination, "Browse", self.browse).grid(row=1, column=2, pady=5)
        tk.Label(destination, text="Dataset", bg=CARD, fg=TEXT, font=FONT).grid(row=2, column=0, sticky="w", pady=5)
        make_entry(destination, self.dataset).grid(row=2, column=1, sticky="ew", padx=10, pady=5)
        tk.Label(destination, text="SQLite is the continuous system of record. CSV and Excel use the current session snapshot.",
                 bg=CARD, fg=MUTED, font=FONT, anchor="w").grid(row=3, column=0, columnspan=3, sticky="ew", pady=(12, 8))
        exports = tk.Frame(destination, bg=CARD)
        exports.grid(row=4, column=0, columnspan=3, sticky="w")
        make_button(exports, "Export session to CSV", lambda: self.export("csv")).pack(side="left")
        make_button(exports, "Export session to Excel", lambda: self.export("xlsx")).pack(side="left", padx=8)

    def _build_live_page(self):
        self.live_page.grid_columnconfigure(0, weight=1)
        self.live_page.grid_rowconfigure(1, weight=1)
        tk.Label(self.live_page, text="LIVE PLC READINGS", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 12, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.live = CanvasTable(self.live_page, ("Area", "PLC", "Signal", "Value", "Quality", "UTC timestamp"),
                                (3, 3, 4, 2, 2, 5), empty_text="Start the engine to receive live readings")
        self.live.grid(row=1, column=0, sticky="nsew")
        self.live_rows = {}

    def show_tab(self, name):
        self.current_tab = name
        self.config_page.grid_forget()
        self.live_page.grid_forget()
        page = self.config_page if name == "configuration" else self.live_page
        page.grid(row=0, column=0, sticky="nsew")
        for widget, selected in ((self.config_button, name == "configuration"), (self.live_button, name == "live")):
            widget.configure(bg=ACCENT if selected else "#E8ECEA", fg="white" if selected else TEXT)

    def refresh_structure(self):
        rows = []
        for area_index, area in enumerate(self.project.areas):
            rows.append((f"area:{area_index}", (area.name, "Area", "")))
            for plc_index, plc in enumerate(area.plcs):
                status = "Disabled" if not plc.enabled else "Ready"
                rows.append((f"plc:{area_index}:{plc_index}", ("   " + plc.name, "PLC", status)))
        self.structure.set_rows(rows)
        self.folder.set(self.project.output_folder)
        self.dataset.set(self.project.dataset)
        self.refresh_signals()

    def _structure_selected(self, key):
        parts = key.split(":")
        self.area_index = int(parts[1])
        self.plc_index = int(parts[2]) if parts[0] == "plc" else None
        self.refresh_signals()

    def refresh_signals(self):
        if self.plc_index is None:
            self.signals.empty_text = "Select a PLC to view signals"
            self.signals.set_rows([], preserve_selection=False)
            return
        plc = self.project.areas[self.area_index].plcs[self.plc_index]
        rows = []
        for index, signal in enumerate(plc.signals):
            address = f"DB{signal.db}.{signal.byte}" + (f".{signal.bit}" if signal.type == "BOOL" else "")
            rows.append((index, (signal.name, address, signal.type, signal.unit, "Yes" if signal.area_state else "No")))
        self.signals.empty_text = "No signals configured for this PLC"
        self.signals.set_rows(rows)

    def add_area(self):
        name = simpledialog.askstring("New area", "Area name:", parent=self.root)
        if name and name.strip():
            self.project.areas.append(Area(name=name.strip()))
            self.area_index = len(self.project.areas) - 1
            self.plc_index = None
            self.refresh_structure()
            self.structure.select(f"area:{self.area_index}")

    def plc_form(self, plc=None):
        plc = plc or Plc()
        dialog = Form(self.root, "PLC configuration",
                      (("Name", "text", ()), ("IP address", "text", ()), ("Rack", "text", ()),
                       ("Slot", "text", ()), ("Poll seconds", "text", ())),
                      (plc.name, plc.ip, plc.rack, plc.slot, plc.poll))
        self.root.wait_window(dialog)
        if not dialog.result:
            return None
        try:
            return Plc(name=dialog.result[0], ip=dialog.result[1], rack=int(dialog.result[2]),
                       slot=int(dialog.result[3]), poll=float(dialog.result[4]), enabled=plc.enabled,
                       signals=plc.signals, id=plc.id)
        except ValueError as exc:
            messagebox.showerror("PLC configuration", f"Rack and slot must be integers; poll must be numeric.\n\n{exc}", parent=self.root)
            return None

    def add_plc(self):
        plc = self.plc_form()
        if plc:
            self.project.areas[self.area_index].plcs.append(plc)
            self.plc_index = len(self.project.areas[self.area_index].plcs) - 1
            self.refresh_structure()
            self.structure.select(f"plc:{self.area_index}:{self.plc_index}")

    def edit_structure(self):
        if self.plc_index is None:
            area = self.project.areas[self.area_index]
            name = simpledialog.askstring("Area", "Area name:", initialvalue=area.name, parent=self.root)
            if name and name.strip():
                area.name = name.strip()
        else:
            current = self.project.areas[self.area_index].plcs[self.plc_index]
            replacement = self.plc_form(current)
            if replacement:
                self.project.areas[self.area_index].plcs[self.plc_index] = replacement
        self.refresh_structure()

    def remove_structure(self):
        if self.plc_index is None:
            if len(self.project.areas) <= 1:
                messagebox.showinfo("Plant structure", "At least one area must remain.", parent=self.root)
                return
            self.project.areas.pop(self.area_index)
            self.area_index = min(self.area_index, len(self.project.areas) - 1)
        else:
            self.project.areas[self.area_index].plcs.pop(self.plc_index)
            self.plc_index = None
        self.refresh_structure()

    def signal_form(self, signal=None):
        signal = signal or Signal()
        dialog = Form(self.root, "Signal configuration",
                      (("Name", "text", ()), ("DB number", "text", ()), ("Start byte", "text", ()),
                       ("Data type", "combo", tuple(TYPES)), ("Bit index", "text", ()),
                       ("Unit", "text", ()), ("Area state", "combo", ("No", "Yes"))),
                      (signal.name, signal.db, signal.byte, signal.type, signal.bit, signal.unit,
                       "Yes" if signal.area_state else "No"))
        self.root.wait_window(dialog)
        if not dialog.result:
            return None
        try:
            return Signal(name=dialog.result[0], db=int(dialog.result[1]), byte=int(dialog.result[2]),
                          type=dialog.result[3], bit=int(dialog.result[4]), unit=dialog.result[5],
                          area_state=dialog.result[6] == "Yes", id=signal.id)
        except ValueError as exc:
            messagebox.showerror("Signal configuration", f"DB, byte and bit must be integers.\n\n{exc}", parent=self.root)
            return None

    def add_signal(self):
        if self.plc_index is None:
            messagebox.showinfo("Signals", "Select a PLC first.", parent=self.root)
            return
        signal = self.signal_form()
        if signal:
            self.project.areas[self.area_index].plcs[self.plc_index].signals.append(signal)
            self.refresh_signals()

    def edit_signal(self):
        index = self.signals.selected_key
        if index is None or self.plc_index is None:
            return
        signals = self.project.areas[self.area_index].plcs[self.plc_index].signals
        replacement = self.signal_form(signals[index])
        if replacement:
            signals[index] = replacement
            self.refresh_signals()

    def remove_signal(self):
        index = self.signals.selected_key
        if index is not None and self.plc_index is not None:
            self.project.areas[self.area_index].plcs[self.plc_index].signals.pop(index)
            self.refresh_signals()

    def collect(self):
        self.project.output_folder = self.folder.get().strip()
        self.project.dataset = self.dataset.get().strip()
        validate(self.project)

    def save_project(self):
        try:
            self.collect()
            save(self.project)
            self.status.set("Configuration saved")
            return True
        except Exception as exc:
            messagebox.showerror("Configuration", str(exc), parent=self.root)
            return False

    def browse(self):
        path = filedialog.askdirectory(parent=self.root, initialdir=self.folder.get() or str(Path.home()))
        if path:
            self.folder.set(path)

    def start(self):
        if self.save_project():
            try:
                self.engine.start(self.project)
                self.status.set("PLC engine starting...")
            except Exception as exc:
                messagebox.showerror("Engine", str(exc), parent=self.root)

    def stop(self):
        self.engine.stop()
        self.status.set("Stopping engine...")

    def scan(self):
        self.status.set("Scanning approved S7 endpoints...")
        threading.Thread(target=self._scan_worker, daemon=True, name="PLCDiscoveryUI").start()

    def _scan_worker(self):
        try:
            devices = scan(self.network.get())
        except Exception as exc:
            self.events.put(("scan_error", str(exc)))
        else:
            self.events.put(("devices", devices))

    def show_devices(self, devices):
        if not devices:
            messagebox.showinfo("PLC scan", "No S7 endpoints found. Check subnet, firewall, PLC access and OT authorization.", parent=self.root)
            return
        added = 0
        for device in devices:
            if all(plc.ip != device["ip"] for area in self.project.areas for plc in area.plcs):
                self.project.areas[self.area_index].plcs.append(Plc(name=device["name"], ip=device["ip"]))
                added += 1
        self.refresh_structure()
        messagebox.showinfo("PLC scan", f"Added {added} new endpoint(s) to {self.project.areas[self.area_index].name}.", parent=self.root)

    def export(self, kind):
        rows = self.engine.snapshot_rows()
        if not rows:
            messagebox.showinfo("Export", "No session readings are available.", parent=self.root)
            return
        extension = "csv" if kind == "csv" else "xlsx"
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension="." + extension,
                                            initialfile=self.dataset.get() + "." + extension)
        if path:
            try:
                (rows_to_csv if kind == "csv" else rows_to_xlsx)(rows, path)
                self.status.set(f"Exported {len(rows)} rows to {path}")
            except Exception as exc:
                messagebox.showerror("Export", str(exc), parent=self.root)

    def process_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind in ("engine", "stored"):
                    self.status.set(str(payload))
                elif kind in ("error", "scan_error"):
                    self.status.set(str(payload))
                    messagebox.showerror("PLC Control", str(payload), parent=self.root)
                elif kind == "devices":
                    self.show_devices(payload)
                elif kind == "areas":
                    self.status.set("Area transitions: " + ", ".join(f"{area}={state}" for area, state in payload))
                elif kind == "interpreter_error":
                    self.status.set("Interpreter: " + str(payload))
                elif kind == "readings":
                    for reading in payload:
                        self.live_rows[reading["signal_id"]] = (
                            reading["area_name"], reading["plc_name"], reading["signal_name"],
                            reading["value"], reading["quality"], reading["timestamp"],
                        )
                    self.live.set_rows(list(self.live_rows.items()))
                elif kind == "plc":
                    plc_id, status = payload
                    for area_index, area in enumerate(self.project.areas):
                        for plc_index, plc in enumerate(area.plcs):
                            if plc.id == plc_id:
                                key = f"plc:{area_index}:{plc_index}"
                                rows = []
                                for row_key, values in self.structure.rows:
                                    rows.append((row_key, (values[0], values[1], status[:32] if row_key == key else values[2])))
                                self.structure.set_rows(rows)
                                break
                elif kind == "stopped":
                    self.status.set("Engine stopped")
        except queue.Empty:
            pass
        if not self._closing and self.root.winfo_exists():
            self.root.after(100, self.process_events)

    def close(self):
        self._closing = True
        self.engine.stop()
        self.engine.wait(timeout=3.0)
        self.root.destroy()


def run():
    root = tk.Tk()
    App(root)
    root.mainloop()
