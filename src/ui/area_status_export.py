"""Excel export engine and Tkinter dialog for area-status SQLite data."""
from __future__ import annotations

import calendar
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

STATE_NAMES = {0: "Fault", 1: "Idle", 2: "Running"}
STATE_COLORS = {0: "D9342B", 1: "7A7A7A", 2: "00B56A"}


@dataclass(frozen=True, slots=True)
class ExportPeriod:
    state_key: int
    start: datetime
    end: datetime

    @property
    def duration_seconds(self) -> float:
        return max(0.0, (self.end - self.start).total_seconds())


def _safe_sheet_name(name: str, used: set[str]) -> str:
    base = re.sub(r"[\\/*?:\[\]]", "_", name).strip() or "Area"
    base = base[:31]
    candidate = base
    number = 2
    while candidate.casefold() in used:
        suffix = f"_{number}"
        candidate = f"{base[:31-len(suffix)]}{suffix}"
        number += 1
    used.add(candidate.casefold())
    return candidate


def _periods_for_area(
    connection: sqlite3.Connection,
    area_id: str,
    start: datetime,
    end: datetime,
) -> list[ExportPeriod]:
    start_ts, end_ts = start.timestamp(), end.timestamp()
    prior = connection.execute(
        """
        SELECT state_key, changed_at FROM area_state_history
        WHERE area_id = ? COLLATE NOCASE AND changed_at < ?
        ORDER BY changed_at DESC, id DESC LIMIT 1
        """,
        (area_id, start_ts),
    ).fetchone()
    rows = connection.execute(
        """
        SELECT state_key, changed_at FROM area_state_history
        WHERE area_id = ? COLLATE NOCASE
          AND changed_at >= ? AND changed_at < ?
        ORDER BY changed_at ASC, id ASC
        """,
        (area_id, start_ts, end_ts),
    ).fetchall()

    transitions: list[tuple[int, float]] = []
    if prior is not None:
        transitions.append((int(prior[0]), start_ts))
    elif rows:
        first_key, first_ts = int(rows[0][0]), float(rows[0][1])
        transitions.append((first_key, max(start_ts, first_ts)))

    for row in rows:
        item = (int(row[0]), max(start_ts, float(row[1])))
        if transitions and item[1] == transitions[-1][1]:
            transitions[-1] = item
        else:
            transitions.append(item)

    periods: list[ExportPeriod] = []
    for index, (state_key, period_start) in enumerate(transitions):
        period_end = transitions[index + 1][1] if index + 1 < len(transitions) else end_ts
        if period_end > period_start:
            periods.append(
                ExportPeriod(
                    state_key,
                    datetime.fromtimestamp(period_start),
                    datetime.fromtimestamp(min(period_end, end_ts)),
                )
            )
    return periods


def export_area_status_excel(
    database_path: str | Path,
    output_folder: str | Path,
    area_ids: list[str],
    start: datetime,
    end: datetime,
) -> Path:
    """Export clipped state intervals to one alphabetically ordered sheet per area."""
    if end <= start:
        raise ValueError("The export end must be after the export start.")
    selected = sorted({str(area).strip() for area in area_ids if str(area).strip()}, key=str.casefold)
    if not selected:
        raise ValueError("Select at least one area.")

    output = Path(output_folder)
    if not output.is_dir():
        raise ValueError("Select an existing output folder.")

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.worksheet.table import Table, TableStyleInfo
    except ImportError as error:
        raise RuntimeError("Excel export requires openpyxl. Install it with: pip install openpyxl") from error

    # Windows forbids ':' in file names, so times use HH-MM while preserving the requested order.
    filename = f"{start:%Y-%m-%d_%H-%M}_{end:%H-%M}.xlsx"
    target = output / filename
    counter = 2
    while target.exists():
        target = output / f"{target.stem}_{counter}.xlsx"
        counter += 1

    workbook = Workbook()
    workbook.remove(workbook.active)
    used_names: set[str] = set()
    thin_gray = Side(style="thin", color="D9E1F2")

    with sqlite3.connect(Path(database_path), timeout=10) as connection:
        for area_id in selected:
            sheet = workbook.create_sheet(_safe_sheet_name(area_id, used_names))
            sheet.sheet_view.showGridLines = False
            sheet.freeze_panes = "A6"
            sheet.merge_cells("A1:F1")
            sheet["A1"] = f"Area Status Export - {area_id}"
            sheet["A1"].font = Font(size=16, bold=True, color="FFFFFF")
            sheet["A1"].fill = PatternFill("solid", fgColor="1F4E78")
            sheet["A1"].alignment = Alignment(horizontal="left")
            sheet["A2"] = "Export start"
            sheet["B2"] = start
            sheet["D2"] = "Export end"
            sheet["E2"] = end
            sheet["A3"] = "State standard"
            sheet["B3"] = "0 = Fault | 1 = Idle | 2 = Running"
            sheet.merge_cells("B3:F3")
            for cell in (sheet["A2"], sheet["D2"], sheet["A3"]):
                cell.font = Font(bold=True, color="666666")
            for cell in (sheet["B2"], sheet["E2"]):
                cell.number_format = "yyyy-mm-dd hh:mm:ss"
                cell.font = Font(color="008000")

            headers = ["State key", "State", "Start", "End", "Duration (seconds)", "Duration (hh:mm:ss)"]
            sheet.append([])
            sheet.append(headers)
            header_row = sheet.max_row
            for cell in sheet[header_row]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="4472C4")
                cell.alignment = Alignment(horizontal="center")

            periods = _periods_for_area(connection, area_id, start, end)
            for period in periods:
                seconds = period.duration_seconds
                sheet.append([
                    period.state_key,
                    STATE_NAMES.get(period.state_key, "Unknown"),
                    period.start,
                    period.end,
                    seconds,
                    seconds / 86400,
                ])
                row = sheet.max_row
                color = STATE_COLORS.get(period.state_key, "FFFFFF")
                sheet.cell(row, 1).fill = PatternFill("solid", fgColor=color)
                sheet.cell(row, 1).font = Font(bold=True, color="FFFFFF")
                sheet.cell(row, 2).font = Font(color="008000")
                sheet.cell(row, 3).number_format = "yyyy-mm-dd hh:mm:ss"
                sheet.cell(row, 4).number_format = "yyyy-mm-dd hh:mm:ss"
                sheet.cell(row, 5).number_format = "0.00"
                sheet.cell(row, 6).number_format = "[h]:mm:ss"

            if periods:
                table_ref = f"A{header_row}:F{sheet.max_row}"
                table = Table(displayName=f"AreaStatus{len(workbook.worksheets)}", ref=table_ref)
                table.tableStyleInfo = TableStyleInfo(
                    name="TableStyleMedium2", showFirstColumn=False,
                    showLastColumn=False, showRowStripes=True, showColumnStripes=False,
                )
                sheet.add_table(table)
            else:
                sheet.append(["", "No status data in the selected interval"])
                sheet.merge_cells(start_row=sheet.max_row, start_column=2, end_row=sheet.max_row, end_column=6)
                sheet.cell(sheet.max_row, 2).font = Font(italic=True, color="C65911")

            for row in sheet.iter_rows(min_row=header_row, max_row=sheet.max_row, min_col=1, max_col=6):
                for cell in row:
                    cell.border = Border(bottom=thin_gray)
                    cell.alignment = Alignment(vertical="center")
            widths = {"A": 12, "B": 18, "C": 22, "D": 22, "E": 20, "F": 20}
            for column, width in widths.items():
                sheet.column_dimensions[column].width = width
            sheet.auto_filter.ref = None  # The table owns filtering when data exists.
            sheet.print_title_rows = f"1:{header_row}"
            sheet.page_setup.orientation = "landscape"
            sheet.sheet_properties.pageSetUpPr.fitToPage = True
            sheet.page_setup.fitToWidth = 1

    workbook.save(target)
    return target


class ModalDatePicker(tk.Toplevel):
    """Dependency-free modal calendar; today is highlighted."""
    def __init__(self, master: tk.Misc, initial: date, title: str = "Select date") -> None:
        super().__init__(master)
        self.title(title)
        self.transient(master)
        self.resizable(False, False)
        self.result: date | None = None
        self._shown_month = initial.replace(day=1)
        self._initial = initial
        self._body = ttk.Frame(self, padding=10)
        self._body.pack(fill="both", expand=True)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._draw()
        self.wait_visibility()
        self.focus_set()

    def _move_month(self, offset: int) -> None:
        month_index = self._shown_month.year * 12 + self._shown_month.month - 1 + offset
        self._shown_month = date(month_index // 12, month_index % 12 + 1, 1)
        self._draw()

    def _choose(self, day: int) -> None:
        self.result = self._shown_month.replace(day=day)
        self.destroy()

    def _draw(self) -> None:
        for child in self._body.winfo_children():
            child.destroy()
        ttk.Button(self._body, text="<", width=3, command=lambda: self._move_month(-1)).grid(row=0, column=0)
        ttk.Label(self._body, text=self._shown_month.strftime("%B %Y"), anchor="center", font=("Segoe UI", 10, "bold")).grid(row=0, column=1, columnspan=5, sticky="ew")
        ttk.Button(self._body, text=">", width=3, command=lambda: self._move_month(1)).grid(row=0, column=6)
        for column, label in enumerate(calendar.day_abbr):
            ttk.Label(self._body, text=label, anchor="center").grid(row=1, column=column, padx=2, pady=3)
        today = date.today()
        for row_index, week in enumerate(calendar.monthcalendar(self._shown_month.year, self._shown_month.month), start=2):
            for column, day_number in enumerate(week):
                if not day_number:
                    continue
                candidate = self._shown_month.replace(day=day_number)
                button = tk.Button(self._body, text=str(day_number), width=4, relief="flat", command=lambda day=day_number: self._choose(day))
                if candidate == today:
                    button.configure(bg="#FFF2CC", activebackground="#FFE699", relief="solid", bd=2)
                elif candidate == self._initial:
                    button.configure(bg="#DDEBF7", activebackground="#BDD7EE")
                button.grid(row=row_index, column=column, padx=1, pady=1)
        ttk.Button(self._body, text="Today", command=lambda: self._choose(today.day) if self._shown_month.year == today.year and self._shown_month.month == today.month else self._go_today()).grid(row=8, column=0, columnspan=7, pady=(8, 0))

    def _go_today(self) -> None:
        self._shown_month = date.today().replace(day=1)
        self._draw()


class AreaStatusExportDialog(tk.Toplevel):
    """Select folder, areas, export mode, dates and optional times."""
    DATE_FORMAT = "%Y-%m-%d"

    def __init__(self, master: tk.Misc, database_path: str | Path, areas: list[str]) -> None:
        super().__init__(master)
        self.database_path = Path(database_path)
        self.title("Export area status to Excel")
        self.transient(master)
        self.geometry("720x560")
        self.minsize(650, 500)
        self.grab_set()

        today = date.today()
        self.folder_var = tk.StringVar()
        self.mode_var = tk.StringVar(value="custom")
        self.start_date_var = tk.StringVar(value=today.strftime(self.DATE_FORMAT))
        self.end_date_var = tk.StringVar(value=today.strftime(self.DATE_FORMAT))
        self.start_time_var = tk.StringVar(value="00:00")
        self.end_time_var = tk.StringVar(value=datetime.now().strftime("%H:%M"))

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(2, weight=1)
        ttk.Label(body, text="Output folder:").grid(row=0, column=0, sticky="w", pady=(0, 8))
        ttk.Entry(body, textvariable=self.folder_var).grid(row=0, column=1, sticky="ew", padx=8, pady=(0, 8))
        ttk.Button(body, text="Browse...", command=self._browse).grid(row=0, column=2, pady=(0, 8))

        ttk.Label(body, text="Areas:").grid(row=1, column=0, sticky="nw")
        area_frame = ttk.Frame(body)
        area_frame.grid(row=1, column=1, columnspan=2, sticky="nsew", pady=(0, 10))
        area_frame.columnconfigure(0, weight=1)
        area_frame.rowconfigure(0, weight=1)
        self.area_list = tk.Listbox(area_frame, selectmode=tk.EXTENDED, exportselection=False, height=10)
        self.area_list.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(area_frame, orient="vertical", command=self.area_list.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.area_list.configure(yscrollcommand=scrollbar.set)
        for area in sorted(areas, key=str.casefold):
            self.area_list.insert(tk.END, area)
        buttons = ttk.Frame(area_frame)
        buttons.grid(row=1, column=0, sticky="w", pady=(5, 0))
        ttk.Button(buttons, text="Select all", command=lambda: self.area_list.selection_set(0, tk.END)).pack(side="left")
        ttk.Button(buttons, text="Clear", command=lambda: self.area_list.selection_clear(0, tk.END)).pack(side="left", padx=5)

        range_box = ttk.LabelFrame(body, text="Export range", padding=10)
        range_box.grid(row=2, column=0, columnspan=3, sticky="nsew")
        ttk.Radiobutton(range_box, text="Custom export (dates and hours)", variable=self.mode_var, value="custom", command=self._update_mode).grid(row=0, column=0, columnspan=5, sticky="w")
        ttk.Radiobutton(range_box, text="Scheduled export (full selected days)", variable=self.mode_var, value="scheduled", command=self._update_mode).grid(row=1, column=0, columnspan=5, sticky="w", pady=(2, 10))
        ttk.Label(range_box, text="Start:").grid(row=2, column=0, sticky="w")
        ttk.Entry(range_box, textvariable=self.start_date_var, width=12, state="readonly").grid(row=2, column=1, padx=(5, 2))
        ttk.Button(range_box, text="Calendar...", command=lambda: self._pick_date(self.start_date_var, "Select start date")).grid(row=2, column=2, padx=(2, 10))
        self.start_time_entry = ttk.Entry(range_box, textvariable=self.start_time_var, width=8)
        self.start_time_entry.grid(row=2, column=3)
        ttk.Label(range_box, text="HH:MM").grid(row=2, column=4, padx=(4, 0))
        ttk.Label(range_box, text="End:").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(range_box, textvariable=self.end_date_var, width=12, state="readonly").grid(row=3, column=1, padx=(5, 2), pady=(8, 0))
        ttk.Button(range_box, text="Calendar...", command=lambda: self._pick_date(self.end_date_var, "Select end date")).grid(row=3, column=2, padx=(2, 10), pady=(8, 0))
        self.end_time_entry = ttk.Entry(range_box, textvariable=self.end_time_var, width=8)
        self.end_time_entry.grid(row=3, column=3, pady=(8, 0))
        ttk.Label(range_box, text="HH:MM").grid(row=3, column=4, padx=(4, 0), pady=(8, 0))
        self.mode_help = ttk.Label(range_box, wraplength=570, foreground="#666666")
        self.mode_help.grid(row=4, column=0, columnspan=5, sticky="w", pady=(12, 0))

        footer = ttk.Frame(body)
        footer.grid(row=3, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(footer, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(footer, text="Export Excel", command=self._export).pack(side="right", padx=8)
        self._update_mode()

    def _browse(self) -> None:
        selected = filedialog.askdirectory(parent=self, title="Select export folder")
        if selected:
            self.folder_var.set(selected)

    def _pick_date(self, variable: tk.StringVar, title: str) -> None:
        current = datetime.strptime(variable.get(), self.DATE_FORMAT).date()
        picker = ModalDatePicker(self, current, title)
        self.wait_window(picker)
        if picker.result:
            variable.set(picker.result.strftime(self.DATE_FORMAT))

    def _update_mode(self) -> None:
        custom = self.mode_var.get() == "custom"
        state = "normal" if custom else "disabled"
        self.start_time_entry.configure(state=state)
        self.end_time_entry.configure(state=state)
        self.mode_help.configure(text=(
            "Custom export uses the exact start and end dates/times."
            if custom else
            "Scheduled export starts at 00:00:00 on the first selected day and ends at 23:59:59 on the last selected day."
        ))

    def _selected_areas(self) -> list[str]:
        return [str(self.area_list.get(index)) for index in self.area_list.curselection()]

    def _range(self) -> tuple[datetime, datetime]:
        start_day = datetime.strptime(self.start_date_var.get(), self.DATE_FORMAT).date()
        end_day = datetime.strptime(self.end_date_var.get(), self.DATE_FORMAT).date()
        if self.mode_var.get() == "scheduled":
            start = datetime.combine(start_day, time.min)
            end = datetime.combine(end_day + timedelta(days=1), time.min)
        else:
            start_clock = datetime.strptime(self.start_time_var.get().strip(), "%H:%M").time()
            end_clock = datetime.strptime(self.end_time_var.get().strip(), "%H:%M").time()
            start = datetime.combine(start_day, start_clock)
            end = datetime.combine(end_day, end_clock)
        return start, end

    def _export(self) -> None:
        try:
            start, end = self._range()
            target = export_area_status_excel(
                self.database_path, self.folder_var.get(), self._selected_areas(), start, end
            )
        except (ValueError, RuntimeError) as error:
            messagebox.showerror("Export failed", str(error), parent=self)
            return
        except OSError as error:
            messagebox.showerror("Export failed", f"Could not write the Excel file:\n{error}", parent=self)
            return
        messagebox.showinfo("Export complete", f"Excel file created:\n{target}", parent=self)