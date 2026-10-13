"""Reusable persistent area-status timeline widget.

State keys:
    0 = Fault
    1 = Idle
    2 = Running

The module is independent from the main application. It uses only Python's
standard library (Tkinter + SQLite), and it can also be run directly to open
a small test window.
"""

from __future__ import annotations

import sqlite3
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable


STATE_NAMES = {
    0: "Fault",
    1: "Idle",
    2: "Running",
}

STATE_COLORS = {
    0: "#D9342B",  # Fault
    1: "#7A7A7A",  # Idle
    2: "#00B56A",  # Running
}

VALID_STATE_KEYS = frozenset(STATE_NAMES)

TIME_RANGE_PRESETS = (
    (30, "30 sec"),
    (60, "1 min"),
    (300, "5 min"),
    (900, "15 min"),
    (3600, "1 hour"),
    (8 * 3600, "8 hours"),
    (24 * 3600, "24 hours"),
    (24 * 3600 * 2, "48 hours"),
    (24 * 3600 * 7, "1 week"),
)

def _default_database_path() -> Path:
    """Return ``<project root>/data/area_status_history.db``.

    The project root is detected by walking upward from this module until the
    existing top-level ``data`` directory is found. This keeps the path valid
    even when the module is moved within the ``src`` package.
    """
    module_path = Path(__file__).resolve()
    for directory in (module_path.parent, *module_path.parents):
        data_directory = directory / "data"
        if data_directory.is_dir():
            return data_directory / "area_status_history.db"

    # Fallback for a normal ``<project root>/src/...`` layout. The directory is
    # created later by AreaStatusStore if it does not already exist.
    project_root = module_path.parent.parent
    return project_root / "data" / "area_status_history.db"


DEFAULT_DATABASE_PATH = _default_database_path()


@dataclass(frozen=True, slots=True)
class StateTransition:
    """One persisted state transition for a production area."""

    area_id: str
    state_key: int
    changed_at: float


class AreaStatusStore:
    """SQLite persistence layer shared by one or more area widgets."""

    def __init__(self, database_path: str | Path = DEFAULT_DATABASE_PATH) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS area_state_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    area_id TEXT NOT NULL,
                    state_key INTEGER NOT NULL CHECK (state_key IN (0, 1, 2)),
                    changed_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_area_state_history_area_time
                ON area_state_history (area_id, changed_at, id)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS area_status_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            mapping_version = connection.execute(
                "SELECT value FROM area_status_metadata WHERE key = 'state_mapping_version'"
            ).fetchone()
            if mapping_version is None:
                # Legacy mapping: 0=Idle, 1=Running, 2=Fault. Convert once so
                # existing history keeps its original semantic meaning.
                connection.execute(
                    """
                    UPDATE area_state_history
                    SET state_key = CASE state_key
                        WHEN 0 THEN 1
                        WHEN 1 THEN 2
                        WHEN 2 THEN 0
                    END
                    """
                )
                connection.execute(
                    """
                    INSERT INTO area_status_metadata (key, value)
                    VALUES ('state_mapping_version', '2')
                    """
                )
            elif mapping_version["value"] != "2":
                raise RuntimeError(
                    "Unsupported area-status state mapping version: "
                    f"{mapping_version['value']}"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS areas (
                    area_id TEXT PRIMARY KEY COLLATE NOCASE,
                    created_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO areas (area_id, created_at)
                SELECT DISTINCT area_id, MIN(changed_at)
                FROM area_state_history
                GROUP BY area_id
                """
            )

    @staticmethod
    def _validate_area_id(area_id: str) -> str:
        clean_area_id = str(area_id).strip()
        if not clean_area_id:
            raise ValueError("area_id cannot be empty")
        return clean_area_id

    @staticmethod
    def _validate_state_key(state_key: int) -> int:
        if isinstance(state_key, bool):
            raise ValueError("state_key must be 0, 1, or 2")
        try:
            normalized = int(state_key)
        except (TypeError, ValueError) as error:
            raise ValueError("state_key must be 0, 1, or 2") from error
        if normalized not in VALID_STATE_KEYS:
            raise ValueError("state_key must be 0, 1, or 2")
        return normalized

    def list_areas(self) -> list[str]:
        """Return all available areas in case-insensitive alphabetical order."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT area_id FROM areas ORDER BY area_id COLLATE NOCASE"
            ).fetchall()
        return [str(row["area_id"]) for row in rows]

    def add_area(self, area_id: str, default_state: int = 1) -> str:
        """Add an area and initialize it with a default state."""
        clean_area_id = self._validate_area_id(area_id)
        state_key = self._validate_state_key(default_state)
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO areas (area_id, created_at) VALUES (?, ?)",
                    (clean_area_id, time.time()),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError(f"Area '{clean_area_id}' already exists") from error
        self.set_state(clean_area_id, state_key, force=True)
        return clean_area_id

    def rename_area(self, old_area_id: str, new_area_id: str) -> str:
        """Rename an area and keep all of its persisted history."""
        old_id = self._validate_area_id(old_area_id)
        new_id = self._validate_area_id(new_area_id)
        if old_id.casefold() == new_id.casefold():
            with self._connect() as connection:
                cursor = connection.execute(
                    "UPDATE areas SET area_id = ? WHERE area_id = ? COLLATE NOCASE",
                    (new_id, old_id),
                )
                connection.execute(
                    "UPDATE area_state_history SET area_id = ? WHERE area_id = ? COLLATE NOCASE",
                    (new_id, old_id),
                )
            if cursor.rowcount == 0:
                raise ValueError(f"Area '{old_id}' does not exist")
            return new_id
        try:
            with self._connect() as connection:
                existing = connection.execute(
                    "SELECT 1 FROM areas WHERE area_id = ? COLLATE NOCASE", (old_id,)
                ).fetchone()
                if existing is None:
                    raise ValueError(f"Area '{old_id}' does not exist")
                connection.execute(
                    "UPDATE areas SET area_id = ? WHERE area_id = ? COLLATE NOCASE",
                    (new_id, old_id),
                )
                connection.execute(
                    "UPDATE area_state_history SET area_id = ? WHERE area_id = ? COLLATE NOCASE",
                    (new_id, old_id),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError(f"Area '{new_id}' already exists") from error
        return new_id

    def remove_area(self, area_id: str) -> None:
        """Remove an area and all of its status history."""
        clean_area_id = self._validate_area_id(area_id)
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM areas WHERE area_id = ? COLLATE NOCASE", (clean_area_id,)
            )
            connection.execute(
                "DELETE FROM area_state_history WHERE area_id = ? COLLATE NOCASE",
                (clean_area_id,),
            )
        if cursor.rowcount == 0:
            raise ValueError(f"Area '{clean_area_id}' does not exist")

    def ensure_area(self, area_id: str, default_state: int = 1) -> int:
        """Register an area and create an initial state when it has no history."""
        clean_area_id = self._validate_area_id(area_id)
        state_key = self._validate_state_key(default_state)
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO areas (area_id, created_at) VALUES (?, ?)",
                (clean_area_id, time.time()),
            )
        current = self.get_current_state(clean_area_id)
        if current is None:
            self.set_state(clean_area_id, state_key, force=True)
            return state_key
        return current

    def get_current_state(self, area_id: str) -> int | None:
        """Return the most recent state, or None for an unknown area."""
        clean_area_id = self._validate_area_id(area_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT state_key
                FROM area_state_history
                WHERE area_id = ?
                ORDER BY changed_at DESC, id DESC
                LIMIT 1
                """,
                (clean_area_id,),
            ).fetchone()
        return None if row is None else int(row["state_key"])

    def set_state(
        self,
        area_id: str,
        state_key: int,
        *,
        changed_at: float | None = None,
        force: bool = False,
    ) -> bool:
        """Persist a transition; return False when the state did not change."""
        clean_area_id = self._validate_area_id(area_id)
        normalized_state = self._validate_state_key(state_key)
        timestamp = time.time() if changed_at is None else float(changed_at)

        if not force and self.get_current_state(clean_area_id) == normalized_state:
            return False

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO area_state_history (area_id, state_key, changed_at)
                VALUES (?, ?, ?)
                """,
                (clean_area_id, normalized_state, timestamp),
            )
        return True

    def get_state_at(self, area_id: str, timestamp: float) -> StateTransition | None:
        """Return the state active at an exact timestamp."""
        clean_area_id = self._validate_area_id(area_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT area_id, state_key, changed_at
                FROM area_state_history
                WHERE area_id = ? AND changed_at <= ?
                ORDER BY changed_at DESC, id DESC
                LIMIT 1
                """,
                (clean_area_id, float(timestamp)),
            ).fetchone()
        if row is None:
            return None
        return StateTransition(
            row["area_id"],
            int(row["state_key"]),
            float(row["changed_at"]),
        )

    def get_time_bounds(self, area_id: str) -> tuple[float, float]:
        """Return the earliest stored timestamp and the current time."""
        clean_area_id = self._validate_area_id(area_id)
        now = time.time()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT MIN(changed_at) AS earliest
                FROM area_state_history
                WHERE area_id = ?
                """,
                (clean_area_id,),
            ).fetchone()
        earliest = now if row is None or row["earliest"] is None else float(row["earliest"])
        return earliest, now

    def get_history(
        self,
        area_id: str,
        *,
        start_time: float,
        end_time: float | None = None,
    ) -> list[StateTransition]:
        """Return transitions in a time window, including the state active at its start."""
        clean_area_id = self._validate_area_id(area_id)
        end = time.time() if end_time is None else float(end_time)
        start = float(start_time)
        if end < start:
            raise ValueError("end_time must be greater than or equal to start_time")

        with self._connect() as connection:
            previous = connection.execute(
                """
                SELECT area_id, state_key, changed_at
                FROM area_state_history
                WHERE area_id = ? AND changed_at < ?
                ORDER BY changed_at DESC, id DESC
                LIMIT 1
                """,
                (clean_area_id, start),
            ).fetchone()
            rows = connection.execute(
                """
                SELECT area_id, state_key, changed_at
                FROM area_state_history
                WHERE area_id = ? AND changed_at >= ? AND changed_at <= ?
                ORDER BY changed_at ASC, id ASC
                """,
                (clean_area_id, start, end),
            ).fetchall()

        history: list[StateTransition] = []
        if previous is not None:
            history.append(
                StateTransition(clean_area_id, int(previous["state_key"]), start)
            )
        history.extend(
            StateTransition(row["area_id"], int(row["state_key"]), float(row["changed_at"]))
            for row in rows
        )
        return history

    def delete_history_before(self, cutoff_time: float) -> int:
        """Delete old rows while retaining the latest prior state for each area."""
        cutoff = float(cutoff_time)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM area_state_history
                WHERE changed_at < ?
                  AND id NOT IN (
                      SELECT id FROM (
                          SELECT id,
                                 ROW_NUMBER() OVER (
                                     PARTITION BY area_id
                                     ORDER BY changed_at DESC, id DESC
                                 ) AS row_number
                          FROM area_state_history
                          WHERE changed_at < ?
                      )
                      WHERE row_number = 1
                  )
                """,
                (cutoff, cutoff),
            )
            return cursor.rowcount


class AreaStatusWidget(ttk.Frame):
    """Reusable widget showing current state and a persistent step timeline."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        area_id: str,
        store: AreaStatusStore | None = None,
        title: str | None = None,
        history_hours: float = 8.0,
        history_seconds: float | None = None,
        refresh_ms: int = 1_000,
        height: int = 145,
        compact: bool = False,
        on_state_changed: Callable[[str, int], None] | None = None,
        on_time_point_selected: Callable[[str, float, int], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        if history_hours <= 0:
            raise ValueError("history_hours must be greater than zero")
        if history_seconds is not None and history_seconds <= 0:
            raise ValueError("history_seconds must be greater than zero")

        self.area_id = str(area_id).strip()
        if not self.area_id:
            raise ValueError("area_id cannot be empty")

        self.store = store or AreaStatusStore()
        self.history_seconds = (
            float(history_seconds)
            if history_seconds is not None
            else float(history_hours) * 3600
        )
        self.refresh_ms = max(250, int(refresh_ms))
        self.compact = bool(compact)
        self.on_state_changed = on_state_changed
        self.on_time_point_selected = on_time_point_selected
        self._after_id: str | None = None

        # None means that the graph follows the current time. A numeric value
        # freezes the right edge of the visible window at that Unix timestamp.
        self._view_end_time: float | None = None
        self._updating_scrollbar = False
        self._selected_timestamp: float | None = None
        self._selected_canvas_y: float | None = None

        current = self.store.ensure_area(self.area_id, default_state=1)
        self._state_key = current

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.rowconfigure(3, weight=0)

        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew", padx=5, pady=(4, 2))
        header.columnconfigure(0, weight=1)

        ttk.Label(
            header,
            text=title or self.area_id.replace("_", " ").title(),
            font=("Segoe UI", 9, "bold"),
        ).grid(row=0, column=0, sticky="w")

        self.state_label = tk.Label(
            header,
            padx=7,
            pady=2,
            fg="white",
            font=("Segoe UI", 8, "bold"),
        )
        self.state_label.grid(row=0, column=1, sticky="e")

        zoom_bar = ttk.Frame(self)
        zoom_bar.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 3))
        zoom_bar.columnconfigure(1, weight=1)

        ttk.Button(zoom_bar, text="−", width=3, command=self.zoom_out).grid(
            row=0, column=0, padx=(0, 4)
        )
        self.time_range_label = ttk.Label(zoom_bar, anchor="center")
        self.time_range_label.grid(row=0, column=1, sticky="ew")
        ttk.Button(zoom_bar, text="+", width=3, command=self.zoom_in).grid(
            row=0, column=2, padx=(4, 0)
        )

        self.selected_point_label = ttk.Label(
            zoom_bar,
            text="Click the graph to inspect a time point",
            anchor="center",
        )
        self.selected_point_label.grid(
            row=1,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=(3, 0),
        )

        self.canvas = tk.Canvas(
            self,
            height=height,
            background="white",
            highlightthickness=1,
            highlightbackground="#C7C7C7",
        )
        self.canvas.grid(row=2, column=0, sticky="nsew", padx=4, pady=(0, 4))
        self.canvas.bind("<Configure>", lambda _event: self.redraw())
        self.canvas.bind("<Button-1>", self._on_graph_click)
        self.canvas.bind("<Button-3>", self._on_graph_right_click)
        self.canvas.bind("<MouseWheel>", self._on_mouse_wheel)
        self.canvas.bind("<Button-4>", lambda _event: self.zoom_in())
        self.canvas.bind("<Button-5>", lambda _event: self.zoom_out())

        navigation_bar = ttk.Frame(self)
        navigation_bar.grid(row=3, column=0, sticky="ew", padx=4, pady=(0, 4))
        navigation_bar.columnconfigure(0, weight=1)

        self.time_scrollbar = ttk.Scrollbar(
            navigation_bar,
            orient="horizontal",
            command=self._on_time_scroll,
        )
        self.time_scrollbar.grid(row=0, column=0, sticky="ew")

        self.live_button = ttk.Button(
            navigation_bar,
            text="Live",
            width=6,
            command=self.go_live,
        )
        self.live_button.grid(row=0, column=1, padx=(6, 0))

        if self.compact:
            # Matrix mode shows only the area header/current state and graph.
            zoom_bar.grid_remove()
            navigation_bar.grid_remove()
            self.canvas.grid_configure(row=1, pady=(0, 2))
            self.rowconfigure(1, weight=1)
            self.rowconfigure(2, weight=0)
            self.rowconfigure(3, weight=0)

        self._update_state_label()
        self._update_time_range_label()
        self._schedule_refresh()

    @property
    def state_key(self) -> int:
        return self._state_key

    @property
    def state_name(self) -> str:
        return STATE_NAMES[self._state_key]

    def set_state(self, state_key: int) -> bool:
        """Public integration method: widget.set_state(0/1/2)."""
        changed = self.store.set_state(self.area_id, state_key)
        self._state_key = self.store.ensure_area(self.area_id)
        self._update_state_label()
        self.redraw()
        if changed and self.on_state_changed is not None:
            self.on_state_changed(self.area_id, self._state_key)
        return changed

    def reload(self) -> None:
        """Reload the current value and history after an external update."""
        self._state_key = self.store.ensure_area(self.area_id)
        self._update_state_label()
        self.redraw()

    def set_matrix_selected(self, selected: bool) -> None:
        """Show or clear the matrix-selection border on the graph."""
        color = "#78BFF5" if selected else "#C7C7C7"
        thickness = 3 if selected else 1
        self.canvas.configure(highlightbackground=color, highlightcolor=color, highlightthickness=thickness)

    def pan_time(self, direction: int, fraction: float = 0.20) -> None:
        """Move the visible window left (-1) or right (+1)."""
        now = time.time()
        current_end = self._view_end_time if self._view_end_time is not None else now
        requested_end = current_end + (1 if direction > 0 else -1) * self.history_seconds * fraction
        self._view_end_time = None if requested_end >= now else requested_end
        self.redraw()

    def set_time_range(self, seconds: float) -> None:
        """Set the visible X-axis duration in seconds."""
        seconds = float(seconds)
        if seconds <= 0:
            raise ValueError("seconds must be greater than zero")
        previous_seconds = self.history_seconds
        if self._view_end_time is not None:
            center_time = self._view_end_time - previous_seconds / 2
            self._view_end_time = center_time + seconds / 2
        self.history_seconds = seconds
        self._update_time_range_label()
        self.redraw()

    def zoom_in(self) -> None:
        """Show a shorter, more detailed time window."""
        values = [seconds for seconds, _label in TIME_RANGE_PRESETS]
        target = values[0]
        for value in values:
            if value < self.history_seconds:
                target = value
            else:
                break
        self.set_time_range(target)

    def zoom_out(self) -> None:
        """Show a longer time window."""
        values = [seconds for seconds, _label in TIME_RANGE_PRESETS]
        target = values[-1]
        for value in values:
            if value > self.history_seconds:
                target = value
                break
        self.set_time_range(target)

    def _on_mouse_wheel(self, event: tk.Event) -> str:
        if event.delta > 0:
            self.zoom_in()
        elif event.delta < 0:
            self.zoom_out()
        return "break"

    def _format_duration(self, seconds: float) -> str:
        if seconds < 60:
            return f"{seconds} second{'s' if seconds != 1 else ''}"

        minutes = round(seconds / 60)
        if minutes < 60:
            return f"{minutes} minute{'s' if minutes != 1 else ''}"

        hours = round(seconds / 3600, 1)
        return f"{hours:g} hour{'s' if hours != 1 else ''}"
       
    def _update_time_range_label(self) -> None:
        self.time_range_label.configure(
            text=f"Visible range: {self._format_duration(self.history_seconds)}"
        )

    def _format_axis_time(self, timestamp: float) -> str:
        moment = datetime.fromtimestamp(timestamp)
        if self.history_seconds <= 120:
            return moment.strftime("%H:%M:%S")
        if self.history_seconds <= 24 * 3600:
            return moment.strftime("%H:%M")
        return moment.strftime("%d %b %H:%M")

    def clear_selected_time_point(self) -> None:
        """Clear the graph marker and selected-time readout."""
        self._selected_timestamp = None
        self._selected_canvas_y = None
        self.selected_point_label.configure(
            text="Click the graph to inspect a time point"
        )
        self.redraw()

    def _on_graph_right_click(self, _event: tk.Event) -> str:
        """Clear the selected graph point and its click overlay."""
        self.clear_selected_time_point()
        return "break"

    def _on_graph_click(self, event: tk.Event) -> str:
        """Select the exact timestamp represented by the clicked X position."""
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        left, right, top, bottom = 56, 8, 10, 22
        plot_width = max(1, width - left - right)
        plot_bottom = height - bottom

        if event.x < left or event.x > width - right or event.y < top or event.y > plot_bottom:
            return "break"

        end_time = min(self._visible_end_time(), time.time())
        start_time = end_time - self.history_seconds
        ratio = min(1.0, max(0.0, (event.x - left) / plot_width))
        selected_timestamp = start_time + ratio * self.history_seconds
        self._selected_canvas_y = float(event.y)
        transition = self.store.get_state_at(self.area_id, selected_timestamp)

        if transition is None:
            self._selected_timestamp = selected_timestamp
            self.selected_point_label.configure(
                text=f"{datetime.fromtimestamp(selected_timestamp):%Y-%m-%d %H:%M:%S}  |  No stored state"
            )
            self.redraw()
            return "break"

        self._selected_timestamp = selected_timestamp
        active_for = max(0.0, selected_timestamp - transition.changed_at)
        self.selected_point_label.configure(
            text=(
                f"{datetime.fromtimestamp(selected_timestamp):%Y-%m-%d %H:%M:%S}  |  "
                f"{STATE_NAMES[transition.state_key]}  |  "
                f"Active for {self._format_duration(round(active_for, 0))}"
            )
        )
        self.redraw()

        if self.on_time_point_selected is not None:
            self.on_time_point_selected(
                self.area_id,
                selected_timestamp,
                transition.state_key,
            )
        return "break"

    @property
    def is_live(self) -> bool:
        """True while the timeline's right edge follows the current time."""
        return self._view_end_time is None

    def go_live(self) -> None:
        """Return to the newest data and resume following the current time."""
        self._view_end_time = None
        self.redraw()

    def _visible_end_time(self) -> float:
        return time.time() if self._view_end_time is None else self._view_end_time

    def _navigation_bounds(self) -> tuple[float, float, float]:
        """Return navigation start, latest time, and total navigable duration."""
        earliest, latest = self.store.get_time_bounds(self.area_id)
        navigation_start = min(earliest, latest - self.history_seconds)
        total_duration = max(self.history_seconds, latest - navigation_start)
        return navigation_start, latest, total_duration

    def _on_time_scroll(self, *arguments: str) -> None:
        """Handle horizontal scrollbar dragging and arrow/page movements."""
        if self._updating_scrollbar or not arguments:
            return

        navigation_start, latest, total_duration = self._navigation_bounds()
        max_window_start = max(navigation_start, latest - self.history_seconds)
        current_end = self._visible_end_time()
        current_start = max(navigation_start, current_end - self.history_seconds)

        if arguments[0] == "moveto" and len(arguments) >= 2:
            fraction = min(1.0, max(0.0, float(arguments[1])))
            requested_start = navigation_start + fraction * total_duration
            window_start = min(max_window_start, requested_start)
        elif arguments[0] == "scroll" and len(arguments) >= 3:
            amount = int(arguments[1])
            units = arguments[2]
            step = self.history_seconds * (0.10 if units == "units" else 0.80)
            window_start = current_start + amount * step
            window_start = min(max_window_start, max(navigation_start, window_start))
        else:
            return

        requested_end = window_start + self.history_seconds
        live_tolerance = max(1.0, self.history_seconds * 0.005)
        self._view_end_time = None if requested_end >= latest - live_tolerance else requested_end
        self.redraw()

    def _update_time_scrollbar(self, view_start: float, view_end: float) -> None:
        """Synchronize the scrollbar thumb with the visible time window."""
        navigation_start, latest, total_duration = self._navigation_bounds()
        first = (view_start - navigation_start) / total_duration
        last = (view_end - navigation_start) / total_duration
        first = min(1.0, max(0.0, first))
        last = min(1.0, max(first, last))

        self._updating_scrollbar = True
        try:
            self.time_scrollbar.set(first, last)
        finally:
            self._updating_scrollbar = False

        self.live_button.configure(state=tk.DISABLED if self.is_live else tk.NORMAL)

    def _update_state_label(self) -> None:
        self.state_label.configure(
            text=STATE_NAMES[self._state_key],
            background=STATE_COLORS[self._state_key],
        )

    def _schedule_refresh(self) -> None:
        if self._after_id is not None:
            self.after_cancel(self._after_id)
        self._after_id = self.after(self.refresh_ms, self._periodic_refresh)

    def _periodic_refresh(self) -> None:
        self._after_id = None
        if self.winfo_exists():
            self.reload()
            self._schedule_refresh()

    def destroy(self) -> None:
        if self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None
        super().destroy()

    def redraw(self) -> None:
        """Render a categorical step graph for the configured time window."""
        canvas = self.canvas
        canvas.delete("all")
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width <= 10 or height <= 10:
            return

        left, right, top, bottom = 56, 8, 10, 22
        plot_width = max(1, width - left - right)
        plot_height = max(1, height - top - bottom)
        now = time.time()
        end_time = min(self._visible_end_time(), now)
        start_time = end_time - self.history_seconds

        y_positions = {
            2: top,                       # Running: top
            1: top + plot_height / 2,     # Idle: middle
            0: top + plot_height,         # Fault: bottom
        }

        for state_key in (2, 1, 0):
            y = y_positions[state_key]
            canvas.create_line(left, y, width - right, y, fill="#E2E2E2")
            canvas.create_text(
                left - 5,
                y,
                text=STATE_NAMES[state_key],
                anchor="e",
                fill=STATE_COLORS[state_key],
                font=("Segoe UI", 7, "bold"),
            )

        history = self.store.get_history(
            self.area_id,
            start_time=start_time,
            end_time=end_time,
        )
        if not history:
            history = [StateTransition(self.area_id, self._state_key, start_time)]
        elif history[0].changed_at > start_time:
            history.insert(0, StateTransition(self.area_id, history[0].state_key, start_time))

        points = list(history)
        points.append(StateTransition(self.area_id, points[-1].state_key, end_time))

        def to_x(timestamp: float) -> float:
            ratio = (timestamp - start_time) / max(1.0, end_time - start_time)
            return left + max(0.0, min(1.0, ratio)) * plot_width

        for current, following in zip(points, points[1:]):
            x1, x2 = to_x(current.changed_at), to_x(following.changed_at)
            y1 = y_positions[current.state_key]
            y2 = y_positions[following.state_key]

            # Tk Canvas does not support alpha fills. Stippling emulates a
            # low-opacity fill while keeping a solid, saturated outline.
            if current.state_key == 2:  # Running: fill below the green line.
                canvas.create_rectangle(
                    x1, y1, x2, y_positions[1],
                    fill=STATE_COLORS[2], stipple="gray25",
                    outline=STATE_COLORS[2], width=1,
                )
            elif current.state_key == 0:  # Fault: fill above the red line.
                canvas.create_rectangle(
                    x1, y_positions[1], x2, y1,
                    fill=STATE_COLORS[0], stipple="gray25",
                    outline=STATE_COLORS[0], width=1,
                )

            canvas.create_line(
                x1, y1, x2, y1,
                fill=STATE_COLORS[current.state_key], width=4,
            )
            if current.state_key != following.state_key:
                canvas.create_line(
                    x2, y1, x2, y2,
                    fill=STATE_COLORS[following.state_key], width=2,
                )

        # Draw five X-axis ticks. Seconds appear for short windows; minutes and
        # hours are selected automatically for larger windows.
        for tick_index in range(5):
            ratio = tick_index / 4
            tick_time = start_time + ratio * (end_time - start_time)
            x = left + ratio * plot_width
            canvas.create_line(
                x,
                top,
                x,
                top + plot_height,
                fill="#F0F0F0",
            )
            anchor = "sw" if tick_index == 0 else "se" if tick_index == 4 else "s"
            canvas.create_text(
                x,
                height - 4,
                text=self._format_axis_time(tick_time),
                anchor=anchor,
                fill="#555555",
                font=("Segoe UI", 7),
            )

        if (
            self._selected_timestamp is not None
            and start_time <= self._selected_timestamp <= end_time
        ):
            selected_ratio = (
                (self._selected_timestamp - start_time)
                / max(1.0, end_time - start_time)
            )
            selected_x = left + selected_ratio * plot_width
            selected_transition = self.store.get_state_at(
                self.area_id,
                self._selected_timestamp,
            )
            canvas.create_line(
                selected_x,
                top,
                selected_x,
                top + plot_height,
                fill="#1F5AA6",
                width=2,
                dash=(4, 2),
                tags="selected-time",
            )
            if selected_transition is not None:
                selected_y = y_positions[selected_transition.state_key]
                canvas.create_oval(
                    selected_x - 5,
                    selected_y - 5,
                    selected_x + 5,
                    selected_y + 5,
                    fill=STATE_COLORS[selected_transition.state_key],
                    outline="#1F5AA6",
                    width=2,
                    tags="selected-time",
                )


            # Click overlay for compact/image-matrix mode.
            click_y = self._selected_canvas_y or (top + plot_height / 2)
            if selected_transition is None:
                overlay_text = f"{datetime.fromtimestamp(self._selected_timestamp):%H:%M:%S}\nNo stored state"
            else:
                active_for = max(0.0, self._selected_timestamp - selected_transition.changed_at)
                overlay_text = (
                    f"{datetime.fromtimestamp(self._selected_timestamp):%H:%M:%S}\n"
                    f"{STATE_NAMES[selected_transition.state_key]} - "
                    f"{self._format_duration(round(active_for))}"
                )

            tooltip = canvas.create_text(
                selected_x + 12, click_y - 12, text=overlay_text,
                anchor="sw", justify="left", fill="white",
                font=("Segoe UI", 8, "bold"), tags="selected-time",
            )
            box = canvas.bbox(tooltip)
            if box is not None:
                x1, y1, x2, y2 = box
                dx = max(min(0, width - x2 - 8), 8 - x1)
                dy = min(max(0, 6 - y1), height - y2 - 6)
                canvas.move(tooltip, dx, dy)
                x1, y1, x2, y2 = canvas.bbox(tooltip)
                background = canvas.create_rectangle(
                    x1 - 7, y1 - 5, x2 + 7, y2 + 5,
                    fill="#1F2937", outline="white", width=1,
                    tags="selected-time",
                )
                canvas.tag_lower(background, tooltip)

        self._update_time_scrollbar(start_time, end_time)


class AreaStatusTestApp:
    """Standalone interface with area management and manual state overrides."""

    DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

    def __init__(self, root: tk.Tk, store: AreaStatusStore | None = None) -> None:
        self.root = root
        self.root.title("Area Status Timeline Test")
        self.root.geometry("920x470")
        self.root.minsize(720, 380)
        # The main application passes its active store so Timeline and the
        # matrix always read the same selected database. Standalone use keeps
        # the original default-store behavior.
        self.store = store if store is not None else AreaStatusStore()
        if not self.store.list_areas():
            self.store.add_area("area_1")

        self.area_id_var = tk.StringVar()
        self.override_time_var = tk.StringVar(value=datetime.now().strftime(self.DATETIME_FORMAT))
        self.state_var = tk.StringVar(value="1 - Idle")
        self.manual_override_enabled = tk.BooleanVar(value=False)

        controls = ttk.LabelFrame(root, text="Test controls", padding=10)
        controls.pack(fill="x", padx=12, pady=(12, 6))
        controls.columnconfigure(1, weight=1)

        ttk.Label(controls, text="Area:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.area_combo = ttk.Combobox(
            controls, textvariable=self.area_id_var, state="readonly", width=25
        )
        self.area_combo.grid(row=0, column=1, sticky="ew", padx=(0, 6))
        self.area_combo.bind("<<ComboboxSelected>>", self._area_selected)
        ttk.Button(controls, text="Edit areas...", command=self._open_area_editor).grid(
            row=0, column=2, sticky="w", padx=(0, 18)
        )

        ttk.Label(controls, text="Manual State Override:").grid(
            row=0, column=3, sticky="w", padx=(0, 6)
        )
        self.time_entry = ttk.Entry(controls, textvariable=self.override_time_var, width=20)
        self.time_entry.grid(row=0, column=4, sticky="w", padx=(0, 6))
        self.state_combo = ttk.Combobox(
            controls,
            textvariable=self.state_var,
            values=tuple(f"{key} - {name}" for key, name in STATE_NAMES.items()),
            state="readonly",
            width=13,
        )
        self.state_combo.grid(row=0, column=5, sticky="w", padx=(0, 8))
        self.apply_override_button = ttk.Button(
            controls, text="Apply override", command=self._apply_override
        )
        self.apply_override_button.grid(row=0, column=6, sticky="e")
        self.time_entry.bind("<Return>", self._apply_override)

        # Manual override safety switch. It starts OFF so historical data cannot
        # be changed accidentally. The adjacent badge is the visual indicator.
        self.override_toggle = ttk.Checkbutton(
            controls,
            text="Enable manual override",
            variable=self.manual_override_enabled,
            command=self._update_manual_override_controls,
        )
        self.override_toggle.grid(row=1, column=3, columnspan=2, sticky="w", pady=(8, 0))
        self.override_status_label = tk.Label(
            controls,
            text="OFF - controls disabled",
            bg="#B42318",
            fg="white",
            font=("Segoe UI", 8, "bold"),
            padx=8,
            pady=2,
        )
        self.override_status_label.grid(row=1, column=5, columnspan=2, sticky="w", pady=(8, 0))

        ttk.Label(
            controls,
            text="Time format: YYYY-MM-DD HH:MM:SS (local time). Overrides are inserted into the timeline at that time.",
        ).grid(row=2, column=0, columnspan=7, sticky="w", pady=(8, 0))

        self.widget_holder = ttk.Frame(root, padding=(12, 6, 12, 12))
        self.widget_holder.pack(fill="both", expand=True)
        self.widget_holder.columnconfigure(0, weight=1)
        self.widget_holder.rowconfigure(0, weight=1)
        self.status_widget: AreaStatusWidget | None = None
        self._update_manual_override_controls()
        self._refresh_area_list()

    def _update_manual_override_controls(self) -> None:
        """Enable or lock all controls that can insert historical overrides."""
        enabled = self.manual_override_enabled.get()
        widget_state = tk.NORMAL if enabled else tk.DISABLED
        self.time_entry.configure(state=widget_state)
        self.state_combo.configure(state="readonly" if enabled else "disabled")
        self.apply_override_button.configure(state=widget_state)
        self.override_status_label.configure(
            text="ON - manual override enabled" if enabled else "OFF - controls disabled",
            bg="#107C10" if enabled else "#B42318",
        )

    def _refresh_area_list(self, preferred: str | None = None) -> None:
        areas = self.store.list_areas()
        self.area_combo.configure(values=areas)
        target = preferred if preferred in areas else self.area_id_var.get()
        if target not in areas:
            target = areas[0] if areas else ""
        self.area_id_var.set(target)
        self._load_area_widget()

    def _area_selected(self, _event: tk.Event | None = None) -> None:
        self._load_area_widget()

    def _load_area_widget(self) -> None:
        area_id = self.area_id_var.get().strip()
        if self.status_widget is not None:
            self.status_widget.destroy()
            self.status_widget = None
        if not area_id:
            return
        self.status_widget = AreaStatusWidget(
            self.widget_holder,
            area_id=area_id,
            store=self.store,
            title=f"Persistent history: {area_id}",
            history_seconds=300,
            relief="ridge",
            padding=4,
        )
        self.status_widget.grid(row=0, column=0, sticky="nsew")

    def _apply_override(self, _event: tk.Event | None = None) -> str | None:
        if not self.manual_override_enabled.get():
            return "break" if _event is not None else None
        area_id = self.area_id_var.get().strip()
        try:
            if not area_id:
                raise ValueError("Select an area first.")
            moment = datetime.strptime(
                self.override_time_var.get().strip(), self.DATETIME_FORMAT
            )
            state_key = int(self.state_var.get().split(" ", 1)[0])
            self.store.set_state(
                area_id, state_key, changed_at=moment.timestamp(), force=True
            )
            if self.status_widget is not None:
                self.status_widget.reload()
            self.override_time_var.set(datetime.now().strftime(self.DATETIME_FORMAT))
        except ValueError as error:
            messagebox.showerror(
                "Invalid override",
                str(error) if str(error) else "Enter a valid time and state.",
                parent=self.root,
            )
        return "break" if _event is not None else None

    def _open_area_editor(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Edit areas")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)
        area_list = tk.Listbox(frame, width=38, height=10, exportselection=False)
        area_list.grid(row=0, column=0, columnspan=3, sticky="nsew", pady=(0, 8))
        name_var = tk.StringVar()
        ttk.Entry(frame, textvariable=name_var, width=38).grid(
            row=1, column=0, columnspan=3, sticky="ew", pady=(0, 8)
        )

        def reload_list(select_name: str | None = None) -> None:
            area_list.delete(0, tk.END)
            areas = self.store.list_areas()
            for area in areas:
                area_list.insert(tk.END, area)
            if select_name in areas:
                index = areas.index(select_name)
                area_list.selection_set(index)
                area_list.see(index)
                name_var.set(select_name)

        def selected_area() -> str:
            selection = area_list.curselection()
            if not selection:
                raise ValueError("Select an area first.")
            return str(area_list.get(selection[0]))

        def on_select(_event: tk.Event | None = None) -> None:
            try:
                name_var.set(selected_area())
            except ValueError:
                pass

        def add_area() -> None:
            try:
                added = self.store.add_area(name_var.get())
                reload_list(added)
                self._refresh_area_list(added)
            except ValueError as error:
                messagebox.showerror("Cannot add area", str(error), parent=dialog)

        def rename_area() -> None:
            try:
                renamed = self.store.rename_area(selected_area(), name_var.get())
                reload_list(renamed)
                self._refresh_area_list(renamed)
            except ValueError as error:
                messagebox.showerror("Cannot rename area", str(error), parent=dialog)

        def remove_area() -> None:
            try:
                area = selected_area()
                if not messagebox.askyesno(
                    "Remove area",
                    f"Remove '{area}' and permanently delete its history?",
                    parent=dialog,
                ):
                    return
                self.store.remove_area(area)
                reload_list()
                self._refresh_area_list()
            except ValueError as error:
                messagebox.showerror("Cannot remove area", str(error), parent=dialog)

        area_list.bind("<<ListboxSelect>>", on_select)
        ttk.Button(frame, text="Add", command=add_area).grid(row=2, column=0, padx=(0, 4))
        ttk.Button(frame, text="Rename", command=rename_area).grid(row=2, column=1, padx=4)
        ttk.Button(frame, text="Remove", command=remove_area).grid(row=2, column=2, padx=(4, 0))
        ttk.Button(frame, text="Close", command=dialog.destroy).grid(
            row=3, column=0, columnspan=3, pady=(10, 0)
        )
        reload_list(self.area_id_var.get())
        dialog.wait_window()

def main() -> None:
    root = tk.Tk()
    AreaStatusTestApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()