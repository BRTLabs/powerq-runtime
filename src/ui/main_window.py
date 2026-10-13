import ctypes
import queue
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from ..models.account_manager import ACCOUNT_TYPES, ACCOUNTS, DEFAULT_ACCOUNT_KEY, save_accounts
from ..models.batch_manager import BATCH_TYPES, save_batch_types
from ..config.big_variables import (
    image_matrix_columns,
    image_matrix_rows,
    company_green,
    pleasent_blue,
)

try:
    from ..config.big_variables import wait_list_font_size
except ImportError:
    wait_list_font_size = 10

from ..models.queue_manager import load_wait_list, save_wait_list
from ..models.status_manager import load_statuses, save_statuses
from .area_status_widget import AreaStatusStore, AreaStatusTestApp, AreaStatusWidget
from .area_status_export import AreaStatusExportDialog
from .matrix_widget_content import resolve_matrix_area_id
from .area_status_database import build_store_for_database
from .status_handling import normalize_area_transitions
from .signal_status import canonical_area_id, raw_value_to_state
from .area_zoom_window import AreaZoomWindow
from .change_view_options import ChangeViewOptions
from plc_control_center.config import load as load_plc_project
from plc_control_center.engine import Engine
from plc_control_center.interpreter_ui import open_interpreter
from plc_control_center.ui import App as PlcControlApp


# Conditional import framework for PIL (Pillow). 
# If Pillow is not installed on the system, the application falls back gracefully without crashing.
try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

# Configure the image resampling filter based on the available Pillow version.
RESAMPLE_FILTER = None
if Image is not None:
    # Handles modern Pillow versions (Pillow 9.1.0+) where Image.Resampling enum is used
    if hasattr(Image, "Resampling"):
        RESAMPLE_FILTER = Image.Resampling.LANCZOS
    else:
        # Fallback for older legacy versions of Pillow
        RESAMPLE_FILTER = Image.LANCZOS

# Configuration constants for queue lifecycle tracking and UI styling
STATUSES = load_statuses()

def status_color(status: str) -> str:
    if status == "Waiting": return "white"
    if status == "Done": return company_green
    return pleasent_blue

# Unicode translation table used to convert standard ASCII numbers into bold mathematical alphanumeric symbols
BOLD_DIGITS = str.maketrans("0123456789", "𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟕𝟴𝟵")

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class BatchQueueApp:
    """
    Main application controller managing the Power Queue Manager workspace.
    Handles layout compilation, context switching, asynchronous window tracking, 
    role-based user authentication states, image display grids, and queue tracking.
    """
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Power Queue Manager")
        self.root.geometry("1280x720")
        self.root.minsize(800, 480)
        self.root.bind("<F11>", self.toggle_fullscreen)
        self.root.bind("<Escape>", self.exit_fullscreen)

        # --- Completely replace the Python App Logo ---
        
        # 1. Tell the OS this is a standalone app so the taskbar icon updates
        try:
            # The string below can be any unique identifier you want
            app_id = 'mycompany.powerqueue.manager.1'
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except AttributeError:
            # Fails gracefully if run on a non-Windows machine
            pass

        # 2. Set the window and taskbar icon
        try:
            # Option A: If you converted your logo to an .ico file (Recommended)
            # self.root.iconbitmap("chosen_logo.ico") 
            
            # Option B: If you are still using a .png file
            app_logo = tk.PhotoImage(file=str(BASE_DIR / "assets" / "images" / "chosen_logo.png"))
            self._app_logo = app_logo
            self.root.iconphoto(True, self._app_logo)
        except tk.TclError:
            print("Warning: chosen_logo image not found or unsupported format.")
            
        # ------------------------------------------

        # Image state arrays tracking UI references and memory pointers to avoid garbage collection
        self.image_labels = []          # UI Label references in the grid
        self.source_images = []         # Uncompressed original PIL Image objects
        self.rendered_images = []       # Active PhotoImage instances scaled to grid cells
        self.image_cells = []           # Per-area containers holding image + timeline
        self.area_status_widgets = []   # Persistent status timeline for every image area
        self.selected_area_widget_index = None
        self._area_widget_event_targets = {}
        self.area_status_store = AreaStatusStore()
        
        # UI Structural component tracking references
        self.left_image_frame = None
        self.image_canvas = None
        self.image_canvas_window = None
        self.image_vertical_scrollbar = None
        self.image_horizontal_scrollbar = None
        self.main_frame = None

        # Matrix view state. Layout values are (visible rows, columns).
        self.matrix_rows = image_matrix_rows
        self.matrix_columns = image_matrix_columns
        self.matrix_zoom = 1.0
        self.matrix_zoom_min = 0.50
        self.matrix_zoom_max = 3.00
        
        # Image fullscreen rendering overlay state trackers
        self.preview_frame = None
        self.preview_label = None
        self.preview_source_image = None
        self.preview_rendered_image = None
        self.preview_active = False
        
        # Context-dependent contextual action containers
        self.status_menu = None
        self._context_batch_id = None    # Temporarily buffers the queue row targeted by right-click
        self._drag_batch_id = None
        self.queue_font_size = wait_list_font_size
        self.fullscreen = False
        self.top_menu_frame = None
        self._admin_click_count = 0
        self._last_admin_click_at = 0.0
        
        # Size tracking tuples used to prevent redundant, expensive image resize operations
        self._last_cell_size = (0, 0)
        self._last_preview_size = (0, 0)
        
        # Profile state tracking initialization
        self.account_key = DEFAULT_ACCOUNT_KEY if DEFAULT_ACCOUNT_KEY in ACCOUNTS else next(iter(ACCOUNTS))
        self.account_name = ACCOUNTS[self.account_key]["username"]
        self.account_type = ACCOUNTS[self.account_key]["account_type"]
        
        # Active sub-window object references (prevents duplicate sub-window creation cascades)
        self.account_window = None
        self.login_window = None
        self.manage_accounts_window = None
        self.batch_editor_window = None
        self.area_timeline_window = None
        self.area_export_window = None
        self.change_view_window = None
        
        # Tkinter-bound tracking variables for dynamic text updates across forms
        self.account_name_var = None
        self.account_type_var = None
        self.account_type_menu_var = None
        
        # Dynamic Menu access configurations
        self.options_menu = None
        self.manage_accounts_button = None

        # PLC acquisition is owned by the Power Queue main window. Network and
        # database work remains off the Tk thread; events are consumed via after().
        self.plc_events = queue.Queue()
        self.plc_engine = Engine(self.plc_events)
        self.plc_project = load_plc_project()
        self.plc_status = tk.StringVar(value="PLC getter stopped")
        self.plc_button_text = tk.StringVar(value="Start PLC getter")
        self.plc_getter_button = None
        self._closing = False
        self.area_zoom_windows = []
        self.latest_signal_readings = {}
        
        # Storage caches for underlying models
        self.accounts_tree = None
        self.batch_types_listbox = None
        self.batch_types = sorted(BATCH_TYPES, key=str.lower)
        
        # Read operations establishing operational queue records
        self.queue_items = load_wait_list()
        self._next_queue_id = self._calculate_next_queue_id()

        # Build application structure and populate visual components
        self._build_layout()
        # The root/toplevel bind tag receives descendant clicks after normal
        # widget processing. Selected timeline clicks are consumed by the
        # first-priority timeline bind tag, while clicks elsewhere reach here.
        self.root.bind("<Button-1>", self._deselect_area_widget_from_outside_click, add="+")
        self.root.bind("<Button-3>", self._deselect_area_widget_from_outside_click, add="+")
        self._refresh_tree()
        
        # Standard system-wide navigation hooks allowing users to step away from image previews
        self.root.bind("<Escape>", self._exit_image_preview)
        self.root.bind("<BackSpace>", self._exit_image_preview)
        self.root.after(100, self._process_plc_events)
        self.root.protocol("WM_DELETE_WINDOW", self._close_application)

    # =========================================================
    # --- 1. Core Layout & UI Utilities ---
    # =========================================================

    def _build_layout(self) -> None:
        """Assembles the primary grid layers, paned configurations, layout segments, and trees."""
        self._build_menu()

        # Primary framework container
        self.main_frame = ttk.Frame(self.root, padding=12)
        self.main_frame.pack(fill=tk.BOTH, expand=True)
        self.main_frame.rowconfigure(0, weight=1)
        self.main_frame.columnconfigure(0, weight=1)

        # Paned splitting interface dividing the visual workspace from item grids
        paned = ttk.Panedwindow(self.main_frame, orient=tk.HORIZONTAL)
        paned.grid(row=0, column=0, sticky="nsew")

        # Left Column Panel - Container housing the image asset viewer
        left_frame = ttk.Frame(paned, relief=tk.SUNKEN, padding=8)
        left_frame.rowconfigure(0, weight=1)
        left_frame.columnconfigure(0, weight=1)
        paned.add(left_frame, weight=3)

        # Scrollable image matrix. The mouse wheel controls only the vertical
        # scrollbar; horizontal movement is intentionally available by dragging
        # the bottom scrollbar.
        matrix_viewport = ttk.Frame(left_frame)
        matrix_viewport.grid(row=0, column=0, sticky="nsew")
        matrix_viewport.rowconfigure(0, weight=1)
        matrix_viewport.columnconfigure(0, weight=1)

        self.image_canvas = tk.Canvas(matrix_viewport, highlightthickness=0, bg="#f0f0f0")
        self.image_vertical_scrollbar = ttk.Scrollbar(
            matrix_viewport, orient=tk.VERTICAL, command=self.image_canvas.yview
        )
        self.image_horizontal_scrollbar = ttk.Scrollbar(
            matrix_viewport, orient=tk.HORIZONTAL, command=self.image_canvas.xview
        )
        self.image_canvas.configure(
            yscrollcommand=self.image_vertical_scrollbar.set,
            xscrollcommand=self.image_horizontal_scrollbar.set,
        )
        self.image_canvas.grid(row=0, column=0, sticky="nsew")
        self.image_vertical_scrollbar.grid(row=0, column=1, sticky="ns")
        self.image_horizontal_scrollbar.grid(row=1, column=0, sticky="ew")

        self.left_image_frame = ttk.Frame(self.image_canvas)
        self.image_canvas_window = self.image_canvas.create_window(
            (0, 0), window=self.left_image_frame, anchor="nw"
        )

        self._build_image_grid()
        self._layout_image_cells()
        self.left_image_frame.bind("<Configure>", self._on_matrix_content_resize)
        self.image_canvas.bind("<Configure>", self._on_left_pane_resize)
        self._bind_matrix_scroll_events(self.image_canvas)
        self._bind_matrix_scroll_events(self.left_image_frame)

        # Right Column Panel - Visual framework housing the operational wait list rows
        right_frame = ttk.LabelFrame(paned, text="Wait List", padding=8)
        right_frame.columnconfigure(0, weight=1)
        right_frame.rowconfigure(0, weight=1)
        paned.add(right_frame, weight=2)

        # Theme styles setting rows sizing parameters and standard font variables
        style = ttk.Style()
        style.configure(
            "WaitList.Treeview",
            rowheight=max(28, wait_list_font_size + 18),
            font=("Segoe UI", wait_list_font_size),
        )
        style.configure(
            "WaitList.Treeview.Heading",
            font=("Segoe UI", wait_list_font_size, "bold"),
        )

        columns = ("sequence", "batch_name", "status")
        self.tree = ttk.Treeview(
            right_frame,
            columns=columns,
            show="headings",
            height=12,
            style="WaitList.Treeview",
        )
        self.tree.heading("sequence", text="Sequence")
        self.tree.heading("batch_name", text="Batch")
        self.tree.heading("status", text="Status")
        self.tree.column("sequence", width=80, anchor="center")
        self.tree.column("batch_name", width=140, anchor="w")
        self.tree.column("status", width=110, anchor="center")
        self.tree.grid(row=0, column=0, sticky="nsew")
        
        # Context structural interface tracking controls bindings
        self.tree.bind("<Button-1>", self._on_tree_left_click)
        self.tree.bind("<B1-Motion>", self._on_tree_drag)
        self.tree.bind("<ButtonRelease-1>", self._on_tree_drop)
        self.tree.bind("<Control-MouseWheel>", self._on_queue_zoom)
        self.tree.bind("<Control-Button-4>", lambda e: self._on_queue_zoom(e, 1))
        self.tree.bind("<Control-Button-5>", lambda e: self._on_queue_zoom(e, -1))
        self.tree.bind("<Button-3>", self._on_tree_right_click)
        
        # Color mapping allocations based on application tracking properties
        for status in STATUSES:
            color = status_color(status)
            self.tree.tag_configure(
                status,
                background=color,
                foreground="black" if status == "Waiting" else "white",
            )

        # Scrollbar integration for tracking oversized queue sets
        scroll = ttk.Scrollbar(right_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")

        # Command framework options housing queue adjustment features
        button_frame = ttk.Frame(right_frame)
        button_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        button_frame.columnconfigure((0, 1), weight=1)

        # 1. Define custom styles for the buttons
        # set theme for the app
        style = ttk.Style()
        # style.theme_use('clam') # Uncomment this if colors don't appear on your OS

        tk.Button(button_frame, text="ADD", bg=company_green, fg="white", command=self.open_add_dialog).grid(
            row=0, column=0, padx=4, sticky="ew"
        )

        tk.Button(button_frame, text="REMOVE", bg=company_green, fg="white", command=self.remove_selected_batch).grid(
            row=0, column=1, padx=4, sticky="ew"
        )

        # Fullscreen high-resolution image preview frame configuration
        self.preview_frame = tk.Frame(left_frame, bg="black")
        self.preview_label = tk.Label(self.preview_frame, bg="black")
        self.preview_label.pack(fill=tk.BOTH, expand=True)

        # Overlay navigation control: it floats over the image instead of
        # reserving a separate bar above the preview.
        self.preview_back_button = tk.Button(
            self.preview_frame,
            text="Back",
            command=self._exit_image_preview,
            bg=company_green,
            fg="white",
            activebackground=company_green,
            activeforeground="white",
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0,
            padx=16,
            pady=6,
            cursor="hand2",
        )
        self.preview_back_button.place(x=12, y=12)
        self.preview_frame.bind("<Configure>", self._on_preview_resize)

        # Right-click dropdown navigation layout targeting row properties
        self.status_menu = tk.Menu(self.root, tearoff=0)
        for status in STATUSES:
            self.status_menu.add_command(
                label=status, command=lambda selected=status: self._set_context_status(selected)
            )

    def _build_menu(self) -> None:
        """Compiles header navigation rows tracking profiles and status items."""
        # Main container for the top menu
        top_menu_frame = ttk.Frame(self.root, padding=(12, 8, 12, 0))
        top_menu_frame.pack(fill=tk.X)
        self.top_menu_frame = top_menu_frame

        # Standard tk.Menubutton styled to mimic a Notepad menu item
        options_button = tk.Menubutton(
            top_menu_frame, 
            text="Options", 
            relief="flat", 
            bd=0,                  # Removes the 3D border shift on click
            highlightthickness=0,  # Removes the dotted focus ring
            bg="#f0f0f0",          # Standard window background color
            activebackground="#e5e5e5", # Subtle hover/click highlight
            padx=8,
            pady=2
        )
        options_button.pack(side=tk.LEFT, anchor="w")

        self.options_menu = tk.Menu(options_button, tearoff=0)
        self.options_menu.add_command(label="Account", command=self.open_account_window)

        # Custom layout dialog implemented in ui/change_view_options.py.
        self.options_menu.add_command(
            label="Change View", command=self.open_change_view_options
        )
        self.options_menu.add_command(
            label="Reset View", command=self._reset_matrix_view
        )
        self.options_menu.add_separator()
        self.options_menu.add_command(
            label="Load Area Database...", command=self.load_area_database
        )
        self.options_menu.add_separator()
        
        # --- Cascade Implementation ---
        self.batches_menu = tk.Menu(self.options_menu, tearoff=0)
        self.options_menu.add_cascade(label="Batches", menu=self.batches_menu)
        self.batches_menu.add_command(label="Configure Batches", command=self.open_batches_window)
        self.batches_menu.add_command(label="Edit Statuses", command=self.open_status_editor)
        self.batches_menu.add_command(label="Reorder Batches", command=self.open_reorder_window)
        self.batches_menu.add_command(label="Reindex Queue Order", command=self.format_queue)
        
        options_button["menu"] = self.options_menu

        # Replaced tk.Button with tk.Label to completely prevent native text shifting
        self.manage_accounts_button = tk.Label(
            top_menu_frame, 
            text="Manage Accounts", 
            bg="#f0f0f0",
            padx=8,
            pady=2
        )
        self.manage_accounts_button.pack(side=tk.LEFT, anchor="w")

        # One-word shortcut for the Area Status Timeline Test window.
        timeline_button = tk.Button(
            top_menu_frame,
            text="Timeline",
            relief="flat",
            bd=0,
            highlightthickness=0,
            bg="#f0f0f0",
            activebackground="#e5e5e5",
            padx=8,
            pady=2,
            command=self.open_area_timeline_window,
        )
        timeline_button.pack(side=tk.LEFT, anchor="w")

        plc_button = tk.Button(top_menu_frame, text="PLC Control", relief="flat", bd=0,
            highlightthickness=0, bg="#f0f0f0", activebackground="#e5e5e5",
            padx=8, pady=2, command=self.open_plc_control)
        plc_button.pack(side=tk.LEFT, anchor="w")

        self.plc_getter_button = tk.Button(
            top_menu_frame,
            textvariable=self.plc_button_text,
            relief="flat",
            bd=0,
            highlightthickness=0,
            bg=company_green,
            fg="white",
            activebackground="#008F50",
            activeforeground="white",
            padx=8,
            pady=2,
            command=self._toggle_plc_getter,
        )
        self.plc_getter_button.pack(side=tk.LEFT, anchor="w", padx=(4, 0))

        tk.Label(
            top_menu_frame,
            textvariable=self.plc_status,
            bg="#f0f0f0",
            fg=pleasent_blue,
            padx=8,
            pady=2,
        ).pack(side=tk.LEFT, anchor="w")

        interpreter_button = tk.Button(top_menu_frame, text="Interpreter", relief="flat", bd=0,
            highlightthickness=0, bg="#f0f0f0", activebackground="#e5e5e5",
            padx=8, pady=2, command=lambda: open_interpreter(self.root))
        interpreter_button.pack(side=tk.LEFT, anchor="w")

        # Direct access to the area-status SQLite-to-Excel export workflow.
        export_button = tk.Button(
            top_menu_frame,
            text="Export",
            relief="flat",
            bd=0,
            highlightthickness=0,
            bg="#f0f0f0",
            activebackground="#e5e5e5",
            padx=8,
            pady=2,
            command=self.open_area_export_window,
        )
        export_button.pack(side=tk.LEFT, anchor="w")

        # Custom event bindings to mimic the exact behavior of the Options menu
        def on_enter(event):
            if self.manage_accounts_button.cget("state") != "disabled":
                self.manage_accounts_button.config(bg="#e5e5e5")

        def on_leave(event):
            if self.manage_accounts_button.cget("state") != "disabled":
                self.manage_accounts_button.config(bg="#f0f0f0")

        def on_click(event):
            if self.manage_accounts_button.cget("state") != "disabled":
                self.open_manage_accounts_window()

        # Bind the hover and click actions directly to the label
        self.manage_accounts_button.bind("<Enter>", on_enter)
        self.manage_accounts_button.bind("<Leave>", on_leave)
        self.manage_accounts_button.bind("<Button-1>", on_click)

        # Right-side role marker panel displaying authorization states
        self.account_type_menu_var = tk.StringVar(value=self.account_type)
        self.account_type_label = tk.Label(
            top_menu_frame,
            textvariable=self.account_type_menu_var,
            fg="gray50",
            anchor="e",
        )
        self.account_type_label.pack(side=tk.RIGHT, anchor="e")
        self.account_type_label.bind("<Button-1>", self._on_admin_text_click)
        
        # Evaluate operational configurations matching role boundaries
        self._update_options_menu_access()
        
    def _on_admin_text_click(self, _event: tk.Event | None = None) -> None:
        """Reveal the project credit after 15 quick consecutive role-label clicks."""
        now = time.monotonic()
        if now - self._last_admin_click_at <= 0.5:
            self._admin_click_count += 1
        else:
            self._admin_click_count = 1
        self._last_admin_click_at = now

        if self._admin_click_count == 15:
            self._admin_click_count = 0
            self._last_admin_click_at = 0.0
            messagebox.showinfo(
                "Despre proiect",
                "Proiect realizat de pp, pentru mai multe clarificari contactati PL sau automatrist.",
                parent=self.root,
            )

    def open_change_view_options(self) -> None:
        """Open or focus the custom image-matrix layout window."""
        if self.change_view_window is not None and self.change_view_window.winfo_exists():
            self.change_view_window.deiconify()
            self.change_view_window.lift()
            self.change_view_window.focus_force()
            return

        dialog = ChangeViewOptions(
            parent=self.root,
            current_rows=self.matrix_rows,
            current_columns=self.matrix_columns,
            on_apply=self._change_matrix_view,
            on_reset_zoom=self._reset_matrix_zoom,
        )
        self.change_view_window = dialog.window
        self.change_view_window.bind(
            "<Destroy>", self._on_change_view_window_destroyed, add="+"
        )

    def _on_change_view_window_destroyed(self, event: tk.Event) -> None:
        if event.widget is self.change_view_window:
            self.change_view_window = None

    def load_area_database(self) -> None:
        """Select an existing SQLite database and rebuild the area matrix."""
        selected = filedialog.askopenfilename(
            parent=self.root,
            title="Load Area Status Database",
            filetypes=(("SQLite databases", "*.db *.sqlite *.sqlite3"), ("All files", "*.*")),
        )
        if not selected:
            return

        try:
            new_store = build_store_for_database(AreaStatusStore, selected)
            # Force a read before replacing the working store. This catches an
            # incompatible schema without losing the currently loaded data.
            areas = new_store.list_areas()
        except Exception as exc:
            messagebox.showerror("Database not loaded", str(exc), parent=self.root)
            return

        old_store = self.area_status_store
        # A Timeline window owns widgets that still reference the old store.
        # Close it before releasing that store. Reopening Timeline below uses
        # the newly selected database through the shared-store constructor.
        if self.area_timeline_window is not None and self.area_timeline_window.winfo_exists():
            self._close_area_timeline_window()
        self.area_status_store = new_store
        close = getattr(old_store, "close", None)
        if callable(close):
            close()
        self._build_image_grid()
        self._refresh_area_zoom_windows()
        messagebox.showinfo(
            "Database loaded",
            f"Loaded {len(areas)} area(s) from:\n{selected}",
            parent=self.root,
        )

    def open_area_export_window(self) -> None:
        """Open or focus the area-status Excel export dialog."""
        if self.area_export_window is not None and self.area_export_window.winfo_exists():
            self.area_export_window.deiconify()
            self.area_export_window.lift()
            self.area_export_window.focus_force()
            return

        self.area_export_window = AreaStatusExportDialog(
            self.root,
            database_path=self.area_status_store.database_path,
            areas=self.area_status_store.list_areas(),
        )
        self.area_export_window.bind(
            "<Destroy>", self._on_area_export_window_destroyed, add="+"
        )

    def _on_area_export_window_destroyed(self, event: tk.Event) -> None:
        if event.widget is self.area_export_window:
            self.area_export_window = None

    def _toggle_plc_getter(self) -> None:
        if self.plc_engine.running:
            self._stop_plc_getter()
        else:
            self._start_plc_getter()

    def _start_plc_getter(self) -> None:
        try:
            self.plc_project = load_plc_project()
            enabled_plcs = [
                plc
                for area in self.plc_project.areas
                for plc in area.plcs
                if plc.enabled
            ]
            configured_signals = sum(len(plc.signals) for plc in enabled_plcs)

            if not enabled_plcs:
                raise ValueError("No enabled PLCs are configured.")
            if configured_signals == 0:
                raise ValueError(
                    "The enabled PLCs have no configured signals. "
                    "Add the PLC DB addresses in PLC Control first."
                )

            self.plc_engine.start(self.plc_project)
            self.plc_button_text.set("Stop PLC getter")
            self.plc_getter_button.configure(bg="#D9342B", activebackground="#B52B24")
            self.plc_status.set("PLC getter starting...")
        except Exception as exc:
            messagebox.showerror("PLC getter", str(exc), parent=self.root)

    def _stop_plc_getter(self) -> None:
        self.plc_engine.stop()
        self.plc_getter_button.configure(state=tk.DISABLED)
        self.plc_status.set("PLC getter stopping...")

    def _process_plc_events(self) -> None:
        try:
            while True:
                event_type, payload = self.plc_events.get_nowait()
                if event_type == "engine":
                    self.plc_status.set(str(payload))
                elif event_type == "plc":
                    _plc_id, status = payload
                    self.plc_status.set(status)
                elif event_type == "stored":
                    self.plc_status.set(f"PLC data stored in {payload}")
                elif event_type == "areas":
                    transitions = normalize_area_transitions(payload)
                    for area_id, state_key in transitions:
                        self.set_area_state(area_id, state_key)
                    self.plc_status.set("Area transitions: " + ", ".join(f"{a}={v}" for a, v in transitions))
                    self._refresh_area_zoom_windows()
                elif event_type == "interpreter_error":
                    self.plc_status.set(f"PLC interpreter: {payload}")
                elif event_type == "error":
                    self.plc_status.set(f"PLC error: {payload}")
                    messagebox.showerror("PLC getter", str(payload), parent=self.root)
                elif event_type == "stopped":
                    self.plc_button_text.set("Start PLC getter")
                    self.plc_getter_button.configure(
                        state=tk.NORMAL,
                        bg=company_green,
                        activebackground="#008F50",
                    )
                    self.plc_status.set("PLC getter stopped")
                elif event_type == "readings":
                    refresh = False
                    for reading in payload:
                        area_id = canonical_area_id(reading.get("area_name", ""))
                        key = (area_id.casefold(), str(reading.get("plc_name", "")), str(reading.get("signal_name", "")))
                        self.latest_signal_readings[key] = reading
                    self._refresh_area_zoom_windows()
        except queue.Empty:
            pass

        if not self._closing and self.root.winfo_exists():
            self.root.after(100, self._process_plc_events)

    def _close_application(self) -> None:
        self._closing = True
        self.plc_engine.stop()
        self.plc_engine.wait(timeout=3.0)
        self.root.destroy()

    def open_plc_control(self) -> None:
        """Open the PLC acquisition center in a large, independent window."""
        if self.plc_engine.running:
            messagebox.showinfo(
                "PLC Control",
                "Stop the PLC getter before opening PLC Control. This prevents duplicate PLC connections and writers.",
                parent=self.root,
            )
            return
        win = tk.Toplevel(self.root)
        win.title("PLC Control Center")
        win.geometry("1380x840")
        win.minsize(1100, 700)
        PlcControlApp(win)

    def open_area_timeline_window(self) -> None:
        """Open or focus the shared Area Status Timeline Test window."""
        if self.area_timeline_window is not None and self.area_timeline_window.winfo_exists():
            self.area_timeline_window.deiconify()
            self.area_timeline_window.lift()
            self.area_timeline_window.focus_force()
            return

        self.area_timeline_window = tk.Toplevel(self.root)
        AreaStatusTestApp(self.area_timeline_window, store=self.area_status_store)
        self.area_timeline_window.transient(self.root)
        self.area_timeline_window.protocol(
            "WM_DELETE_WINDOW", self._close_area_timeline_window
        )
        self._center_window_on_screen(self.area_timeline_window, 920, 470)

    def _close_area_timeline_window(self) -> None:
        if self.area_timeline_window is not None:
            self.area_timeline_window.destroy()
            self.area_timeline_window = None

    def _center_window_on_screen(
        self, window: tk.Toplevel, width: int | None = None, height: int | None = None
    ) -> None:
        """Utility geometry calculator positioning window panels dynamically in screen centers."""
        window.update_idletasks()
        target_width = width if width is not None else window.winfo_reqwidth()
        target_height = height if height is not None else window.winfo_reqheight()
        x = max(0, (window.winfo_screenwidth() - target_width) // 2)
        y = max(0, (window.winfo_screenheight() - target_height) // 2)
        window.geometry(f"{target_width}x{target_height}+{x}+{y}")


    # =========================================================
    # --- 2. Image Grid & Preview Management ---
    # =========================================================

    def _build_image_grid(self) -> None:
        """Build the matrix entirely from compact area-status graphs."""
        for child in self.left_image_frame.winfo_children():
            child.destroy()
        self.image_labels.clear()
        self.source_images.clear()
        self.rendered_images.clear()
        self.image_cells.clear()
        self.area_status_widgets.clear()
        self.selected_area_widget_index = None
        self._area_widget_event_targets.clear()

        visible_slots = self.matrix_rows * self.matrix_columns
        stored_areas = self.area_status_store.list_areas()
        for index in range(visible_slots):
            # A matrix slot is displayed only when that area already exists in
            # the selected database. No fallback area ID is ever generated.
            area_id = resolve_matrix_area_id(index=index, available_areas=stored_areas)
            if area_id is None:
                break

            row, col = divmod(index, self.matrix_columns)
            cell = ttk.Frame(self.left_image_frame, relief=tk.RIDGE, padding=2)
            cell.grid(row=row, column=col, sticky="nsew", padx=2, pady=2)
            cell.columnconfigure(0, weight=1)
            cell.rowconfigure(0, weight=1)
            self.image_cells.append(cell)

            timeline = AreaStatusWidget(
                cell, area_id=area_id, store=self.area_status_store,
                title=area_id.replace("_", " ").title(), history_hours=8,
                height=110, compact=True, padding=0,
            )
            timeline.grid(row=0, column=0, sticky="nsew")
            self.area_status_widgets.append(timeline)
            self._bind_matrix_scroll_events(cell)
            self._bind_area_widget_interaction(timeline, index)
            cell.bind("<Button-3>", lambda event, selected=index: (self._set_selected_area_widget(None), self._show_image_widget_menu(event, selected))[1])

        self._last_cell_size = (0, 0)
        self._layout_image_cells()
        self._update_image_grid()

    def _deselect_area_widget_from_outside_click(self, event: tk.Event) -> None:
        """Clear timeline selection for any click outside area widgets."""
        if self.selected_area_widget_index is None:
            return
        if str(event.widget) not in self._area_widget_event_targets:
            self._set_selected_area_widget(None)

    def _bind_area_widget_interaction(self, timeline: AreaStatusWidget, index: int) -> None:
        """Install a first-priority bind tag on every part of a compact timeline.

        The previous implementation used normal widget bindings. The Canvas had
        its own wheel handler returning ``break``, so Tk stopped dispatch before
        the matrix handler could run. A custom bind tag placed first avoids that
        binding-order conflict.
        """
        tag = "PowerQAreaMatrixTimeline"
        if not getattr(self, "_area_matrix_bindtag_ready", False):
            self.root.bind_class(tag, "<Button-1>", self._on_area_matrix_left_click)
            self.root.bind_class(tag, "<Double-Button-1>", self._on_area_matrix_double_click)
            self.root.bind_class(tag, "<Button-3>", self._on_area_matrix_right_click)
            self.root.bind_class(tag, "<MouseWheel>", self._on_area_matrix_wheel)
            self.root.bind_class(tag, "<Control-MouseWheel>", self._on_area_matrix_ctrl_wheel)
            self.root.bind_class(tag, "<Button-4>", self._on_area_matrix_wheel)
            self.root.bind_class(tag, "<Button-5>", self._on_area_matrix_wheel)
            self.root.bind_class(tag, "<Control-Button-4>", self._on_area_matrix_ctrl_wheel)
            self.root.bind_class(tag, "<Control-Button-5>", self._on_area_matrix_ctrl_wheel)
            self._area_matrix_bindtag_ready = True

        def tag_tree(widget: tk.Misc) -> None:
            tags = tuple(current for current in widget.bindtags() if current != tag)
            widget.bindtags((tag, *tags))
            self._area_widget_event_targets[str(widget)] = index
            for child in widget.winfo_children():
                tag_tree(child)

        tag_tree(timeline)

    def _area_widget_index_for_event(self, event: tk.Event) -> int | None:
        return self._area_widget_event_targets.get(str(event.widget))

    def _on_area_matrix_left_click(self, event: tk.Event) -> str:
        index = self._area_widget_index_for_event(event)
        if index is not None:
            self._set_selected_area_widget(index)
        return "break"

    def _on_area_matrix_double_click(self, event: tk.Event) -> str:
        """Open an independent pure-Tk zoom window for the selected area."""
        index = self._area_widget_index_for_event(event)
        if index is not None and index < len(self.area_status_widgets):
            area_id = self.area_status_widgets[index].area_id
            zoom = AreaZoomWindow(
                self.root,
                area_id,
                rows_provider=lambda selected=area_id: self._area_zoom_data(selected),
                on_close=self._forget_area_zoom_window,
            )
            self.area_zoom_windows.append(zoom)
        return "break"

    def _forget_area_zoom_window(self, zoom: AreaZoomWindow) -> None:
        if zoom in self.area_zoom_windows:
            self.area_zoom_windows.remove(zoom)

    def _refresh_area_zoom_windows(self) -> None:
        for zoom in list(self.area_zoom_windows):
            if zoom.window.winfo_exists():
                zoom.refresh()
            else:
                self._forget_area_zoom_window(zoom)

    def _project_area_for_status_id(self, area_id):
        return next((area for area in self.plc_project.areas if canonical_area_id(area.name).casefold() == area_id.casefold()), None)

    def _area_zoom_data(self, area_id):
        overall_state = self.area_status_store.get_current_state(area_id)
        rows = []
        area = self._project_area_for_status_id(area_id)
        if area is not None:
            for plc in area.plcs:
                for signal in plc.signals:
                    reading = self.latest_signal_readings.get((area_id.casefold(), plc.name, signal.name))
                    raw = None if reading is None else reading.get("value")
                    quality = "NO DATA" if reading is None else str(reading.get("quality", "UNKNOWN"))
                    state = raw_value_to_state(raw) if quality == "GOOD" else None
                    rows.append((signal.name, plc.name, state, raw))
        return overall_state, rows

    def _on_area_matrix_right_click(self, event: tk.Event) -> str:
        index = self._area_widget_index_for_event(event)
        self._set_selected_area_widget(None)
        if index is not None:
            return self._show_image_widget_menu(event, index)
        return "break"

    def _set_selected_area_widget(self, index: int | None) -> None:
        if index is not None and not 0 <= index < len(self.area_status_widgets):
            index = None
        self.selected_area_widget_index = index
        for current, widget in enumerate(self.area_status_widgets):
            widget.set_matrix_selected(current == index)

    @staticmethod
    def _wheel_up(event: tk.Event) -> bool:
        return getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0

    def _on_area_matrix_wheel(self, event: tk.Event) -> str:
        index = self._area_widget_index_for_event(event)
        if index is None or self.selected_area_widget_index != index:
            return self._on_matrix_mousewheel(event)
        # Wheel up moves to older data (left); wheel down returns toward live (right).
        self.area_status_widgets[index].pan_time(-1 if self._wheel_up(event) else 1)
        return "break"

    def _on_area_matrix_ctrl_wheel(self, event: tk.Event) -> str:
        index = self._area_widget_index_for_event(event)
        if index is None or self.selected_area_widget_index != index:
            return self._on_matrix_zoom_mousewheel(event)
        timeline = self.area_status_widgets[index]
        if self._wheel_up(event):
            timeline.zoom_in()
        else:
            timeline.zoom_out()
        return "break"

    def _show_image_widget_menu(self, event: tk.Event, index: int) -> str:
        """Open the image-widget context menu at the pointer position."""
        if not 0 <= index < len(self.image_cells):
            return "break"
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(
            label="Remove Widget",
            command=lambda selected=index: self._remove_image_widget(selected),
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _remove_image_widget(self, index: int) -> None:
        """Remove one visible status graph and reflow the remaining cells."""
        if not 0 <= index < len(self.image_cells):
            return
        self.image_cells[index].destroy()
        self.image_cells.pop(index)
        self.area_status_widgets.pop(index)
        self._last_cell_size = (0, 0)
        self._layout_image_cells()
        self._rebind_image_widget_indexes()
        self._update_image_grid()

    def _rebind_image_widget_indexes(self) -> None:
        """Refresh context-menu bindings after graph cells are removed."""
        for index, cell in enumerate(self.image_cells):
            cell.bind("<Button-3>", lambda event, selected=index: self._show_image_widget_menu(event, selected))

    def set_area_state(self, area_id: str, state_key: int) -> bool:
        """Update an area's persistent timeline from the production logic (0/1/2)."""
        normalized_id = str(area_id).strip()
        for widget in self.area_status_widgets:
            if widget.area_id == normalized_id:
                return widget.set_state(state_key)
        return self.area_status_store.set_state(normalized_id, state_key)

    def _get_image_paths(self) -> list[Path]:
        """Scans folder pathways resolving up to 8 valid asset items alphabetically."""
        image_dir = Path(__file__).resolve().parent.parent.parent / "assets" / "images"
        valid_extensions = {".png", ".jpg", ".jpeg", ".gif", ".bmp"}
        if not image_dir.exists():
            return []

        files = [
            path
            for path in sorted(image_dir.iterdir(), key=lambda current: current.name.lower())
            if path.is_file() and path.suffix.lower() in valid_extensions
        ]
        return files

    def _bind_matrix_scroll_events(self, widget: tk.Misc) -> None:
        """Bind exclusive Ctrl+wheel zoom and ordinary vertical scrolling."""
        widget.bind("<Control-MouseWheel>", self._on_matrix_zoom_mousewheel)
        widget.bind("<Control-Button-4>", self._on_matrix_zoom_mousewheel)
        widget.bind("<Control-Button-5>", self._on_matrix_zoom_mousewheel)
        widget.bind("<MouseWheel>", self._on_matrix_mousewheel)
        widget.bind("<Button-4>", self._on_matrix_mousewheel)
        widget.bind("<Button-5>", self._on_matrix_mousewheel)
        for child in widget.winfo_children():
            self._bind_matrix_scroll_events(child)

    def _on_matrix_content_resize(self, _event: tk.Event | None = None) -> None:
        """Keep Canvas scroll limits synchronized with the complete matrix."""
        if self.image_canvas is not None:
            self.image_canvas.configure(scrollregion=self.image_canvas.bbox("all"))

    def _on_left_pane_resize(self, _event: tk.Event | None = None) -> None:
        """Recalculate cell dimensions when the matrix viewport changes."""
        self._last_cell_size = (0, 0)
        self._layout_image_cells()
        self._update_image_grid()

    def _change_matrix_view(self, rows: int, columns: int) -> None:
        """Load and display only the first rows x columns image slots."""
        self.matrix_rows = max(1, rows)
        self.matrix_columns = max(1, columns)
        self._build_image_grid()
        self.image_canvas.xview_moveto(0)
        self.image_canvas.yview_moveto(0)

    def _reset_matrix_view(self) -> None:
        """Restore the standard configured matrix layout and zoom level."""
        self.matrix_rows = image_matrix_rows
        self.matrix_columns = image_matrix_columns
        self.matrix_zoom = 1.0
        self._build_image_grid()
        self.image_canvas.xview_moveto(0)
        self.image_canvas.yview_moveto(0)

    def _layout_image_cells(self) -> None:
        """Position cells and assign their zoom-aware requested dimensions."""
        if self.image_canvas is None:
            return
        self.image_canvas.update_idletasks()
        viewport_width = max(1, self.image_canvas.winfo_width())
        viewport_height = max(1, self.image_canvas.winfo_height())
        base_width = max(150, viewport_width // self.matrix_columns)
        base_height = max(130, viewport_height // self.matrix_rows)
        cell_width = max(90, int(base_width * self.matrix_zoom))
        cell_height = max(100, int(base_height * self.matrix_zoom))

        for index, cell in enumerate(self.image_cells):
            row, column = divmod(index, self.matrix_columns)
            cell.grid(row=row, column=column, sticky="nsew", padx=2, pady=2)
            cell.configure(width=cell_width, height=cell_height)
            cell.grid_propagate(False)

        for row in range(max(1, (len(self.image_cells) + self.matrix_columns - 1) // self.matrix_columns)):
            self.left_image_frame.rowconfigure(row, minsize=cell_height)
        for column in range(self.matrix_columns):
            self.left_image_frame.columnconfigure(column, minsize=cell_width)

        requested_width = self.matrix_columns * cell_width + 4
        requested_rows = max(1, (len(self.image_cells) + self.matrix_columns - 1) // self.matrix_columns)
        requested_height = requested_rows * cell_height + 4
        self.image_canvas.itemconfigure(
            self.image_canvas_window, width=requested_width, height=requested_height
        )
        self.root.after_idle(self._on_matrix_content_resize)

    def _on_matrix_zoom_mousewheel(self, event: tk.Event) -> str:
        """Zoom exclusively and consume the gesture before any scrolling."""
        wheel_up = getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0
        self._zoom_matrix(1.10 if wheel_up else 1 / 1.10)
        return "break"

    def _on_matrix_mousewheel(self, event: tk.Event) -> str:
        """Scroll vertically only when Ctrl is not pressed."""
        if bool(getattr(event, "state", 0) & 0x0004):
            return "break"
        wheel_up = getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0
        self.image_canvas.yview_scroll(-1 if wheel_up else 1, "units")
        return "break"

    def _zoom_matrix(self, factor: float) -> None:
        """Zoom matrix cells while preserving a practical min/max range."""
        new_zoom = max(self.matrix_zoom_min, min(self.matrix_zoom_max, self.matrix_zoom * factor))
        if abs(new_zoom - self.matrix_zoom) < 0.001:
            return
        self.matrix_zoom = new_zoom
        self._last_cell_size = (0, 0)
        self._layout_image_cells()
        self._update_image_grid()

    def _reset_matrix_zoom(self) -> None:
        self.matrix_zoom = 1.0
        self._last_cell_size = (0, 0)
        self._layout_image_cells()
        self._update_image_grid()

    def _update_image_grid(self) -> None:
        """Redraw all area-status graphs after layout or zoom changes."""
        for widget in self.area_status_widgets:
            if widget.winfo_exists():
                widget.redraw()

    def _show_image_preview(self, index: int) -> None:
        """Hides the main split configuration screen and transitions to a full-window image workspace overlay."""
        if Image is None or ImageTk is None:
            return

        if index >= len(self.source_images):
            return
        selected_image = self.source_images[index]
        if selected_image is None:
            return

        self.preview_source_image = selected_image
        self._last_preview_size = (0, 0)
        self.preview_active = True
        
        # Swap screen visibility contexts
        self.image_canvas.master.grid_remove()
        self.preview_frame.grid(row=0, column=0, sticky="nsew")
        self.preview_back_button.lift()
        self._update_preview_image()

    def _on_preview_resize(self, _event: tk.Event) -> None:
        """Dynamic size tracker running inside fullscreen modes to control element matching loops."""
        self._update_preview_image()

    def _update_preview_image(self) -> None:
        """Re-scales target sources to match full width parameters during window extensions."""
        if not self.preview_active or self.preview_source_image is None:
            return

        self.preview_frame.update_idletasks()
        width = self.preview_frame.winfo_width()
        height = self.preview_frame.winfo_height()
        if width <= 0 or height <= 0:
            return

        # Performance Optimization: Skip recalculating frame arrays if canvas dimensions match current constraints
        if self._last_preview_size == (width, height):
            return
        self._last_preview_size = (width, height)

        resized = self.preview_source_image.resize((width, height), RESAMPLE_FILTER)
        self.preview_rendered_image = ImageTk.PhotoImage(resized)
        self.preview_label.configure(image=self.preview_rendered_image)

    def _exit_image_preview(self, _event: tk.Event | None = None) -> None:
        """Tears down the full-screen image preview workspace layer and restores the primary grid view."""
        if not self.preview_active:
            return

        self.preview_active = False
        self.preview_source_image = None
        self.preview_rendered_image = None
        self.preview_label.configure(image="")
        
        # Restore normal operational visibility mappings
        self.preview_frame.grid_remove()
        self.image_canvas.master.grid(row=0, column=0, sticky="nsew")
        self._last_cell_size = (0, 0)
        self._layout_image_cells()
        self._update_image_grid()


    # =========================================================
    # --- 3. Queue & Wait List Operations ---
    # =========================================================

    def _refresh_tree(self) -> None:
        """Clears all Treeview entries, then loops through the wait list data to render active items."""
        self.tree.delete(*self.tree.get_children())
        for sequence, item in enumerate(self.queue_items, start=1):
            status_key = item["status"] if item["status"] in STATUSES else STATUSES[0]
            self.tree.insert(
                "",
                "end",
                iid=item["id"],
                values=(
                    self._format_sequence_number(sequence), # Render bold unicode tracking numbers
                    item["batch_name"],
                    item["status"],
                ),
                tags=(status_key,),
            )

    def _format_sequence_number(self, sequence: int) -> str:
        """Uses translation mappings to output stylised unicode bold numbers in status items."""
        return str(sequence).translate(BOLD_DIGITS)

    def _calculate_next_queue_id(self) -> int:
        """Scans current lists parsing trailing numeric values out of 'queue-X'.
        Resets to 1 ONLY if the queue is completely empty.
        """
        # Dacă nu avem niciun element, resetăm numărătoarea la 1 în siguranță
        if not self.queue_items:
            return 1

        max_id = 0
        for item in self.queue_items:
            queue_id = item.get("id", "")
            if isinstance(queue_id, str) and queue_id.startswith("queue-"):
                suffix = queue_id.removeprefix("queue-")
                if suffix.isdigit():
                    max_id = max(max_id, int(suffix))
                    
        return max_id + 1

    def _create_queue_item(self, batch_name: str, status: str) -> dict[str, str]:
        """Constructs tracked operational metadata structures representing unique item entries."""
        queue_item = {
            "id": f"queue-{self._next_queue_id}",
            "batch_name": batch_name,
            "status": status,
        }
        self._next_queue_id += 1
        return queue_item

    def _persist_wait_list(self) -> None:
        """Pushes current task items structure back into persistent JSON file systems."""
        save_wait_list(self.queue_items)

    def _apply_queue_font(self) -> None:
        style = ttk.Style()
        style.configure("WaitList.Treeview", rowheight=max(28, self.queue_font_size + 18), font=("Segoe UI", self.queue_font_size))
        style.configure("WaitList.Treeview.Heading", font=("Segoe UI", self.queue_font_size, "bold"))

    def _on_queue_zoom(self, event: tk.Event, direction: int | None = None) -> str:
        step = direction if direction is not None else (1 if getattr(event, "delta", 0) > 0 else -1)
        self.queue_font_size = max(7, min(28, self.queue_font_size + step))
        self._apply_queue_font()
        return "break"

    def _on_tree_drag(self, event: tk.Event) -> str | None:
        if self._drag_batch_id is None:
            self._drag_batch_id = self.tree.identify_row(event.y) or None
        target = self.tree.identify_row(event.y)
        if self._drag_batch_id and target and target != self._drag_batch_id:
            ids = [item["id"] for item in self.queue_items]
            old, new = ids.index(self._drag_batch_id), ids.index(target)
            item = self.queue_items.pop(old)
            self.queue_items.insert(new, item)
            self._refresh_tree()
            self.tree.selection_set(self._drag_batch_id)
        return "break" if self._drag_batch_id else None

    def _on_tree_drop(self, _event: tk.Event) -> None:
        if self._drag_batch_id is not None:
            self._persist_wait_list()
        self._drag_batch_id = None

    def toggle_fullscreen(self, _event: tk.Event | None = None) -> str:
        self.fullscreen = not self.fullscreen
        self.root.attributes("-fullscreen", self.fullscreen)
        if self.top_menu_frame is not None:
            if self.fullscreen:
                self.top_menu_frame.pack_forget()
            else:
                self.top_menu_frame.pack(fill=tk.X, before=self.main_frame)
        return "break"

    def exit_fullscreen(self, _event: tk.Event | None = None) -> str:
        if self.fullscreen:
            return self.toggle_fullscreen()
        return "break"

    def _rebuild_status_ui(self) -> None:
        self.status_menu.delete(0, tk.END)
        for status in STATUSES:
            self.status_menu.add_command(label=status, command=lambda value=status: self._set_context_status(value))
            self.tree.tag_configure(status, background=status_color(status), foreground="black" if status == "Waiting" else "white")
        self._refresh_tree()

    def open_status_editor(self) -> None:
        dialog = tk.Toplevel(self.root); dialog.title("Edit Statuses"); dialog.transient(self.root); dialog.grab_set()
        frame = ttk.Frame(dialog, padding=16); frame.pack(fill="both", expand=True)
        values = tk.Listbox(frame, width=36, height=12, exportselection=False); values.grid(row=0, column=0, columnspan=3, sticky="nsew")
        entry = ttk.Entry(frame); entry.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        def refresh(selected=None):
            values.delete(0, tk.END)
            for status in STATUSES: values.insert(tk.END, status)
            if selected in STATUSES: values.selection_set(STATUSES.index(selected))
        def add():
            status = entry.get().strip()
            if not status or status in STATUSES: return
            STATUSES.append(status); save_statuses(STATUSES); entry.delete(0, tk.END); refresh(status); self._rebuild_status_ui()
        def remove():
            selected = values.curselection()
            if not selected: return
            status = STATUSES[selected[0]]
            if any(item["status"] == status for item in self.queue_items):
                messagebox.showwarning("Status in use", "Change batches using this status before deleting it.", parent=dialog); return
            if len(STATUSES) == 1: return
            STATUSES.remove(status); save_statuses(STATUSES); refresh(); self._rebuild_status_ui()
        ttk.Button(frame, text="Add", command=add).grid(row=1, column=1, padx=6, pady=(10, 0))
        ttk.Button(frame, text="Delete", command=remove).grid(row=1, column=2, pady=(10, 0))
        ttk.Button(frame, text="Close", command=dialog.destroy).grid(row=2, column=2, sticky="e", pady=(12, 0))
        frame.columnconfigure(0, weight=1); refresh(); entry.focus_set(); self._center_window_on_screen(dialog)

    def open_add_dialog(self) -> None:
        """Initializes batch item allocation framework to let operators inject tasks into queues."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Add Batches")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.columnconfigure(0, weight=1)
        dialog.columnconfigure(1, weight=1)

        ttk.Label(dialog, text="Choose batch to add:").grid(
            row=0, column=0, columnspan=2, padx=12, pady=(12, 6), sticky="w"
        )
        list_container = ttk.Frame(dialog)
        list_container.grid(row=1, column=0, columnspan=2, padx=12, pady=6, sticky="nsew")
        list_container.rowconfigure(0, weight=1)
        list_container.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)

        batch_list = tk.Listbox(list_container, exportselection=False, height=8)
        batch_list.grid(row=0, column=0, sticky="nsew")
        for batch_name in self.batch_types:
            batch_list.insert(tk.END, batch_name)
        if self.batch_types:
            batch_list.selection_set(0)

        list_scroll = ttk.Scrollbar(list_container, orient="vertical", command=batch_list.yview)
        batch_list.configure(yscrollcommand=list_scroll.set)
        list_scroll.grid(row=0, column=1, sticky="ns")

        # Core logic execution mapping for appending item selections
        def add_batch() -> None:
            if not self.batch_types:
                messagebox.showwarning(
                    "No batch types", "No batch types available. Please create one in Batch Editor."
                )
                return
            selected_indices = batch_list.curselection()
            if not selected_indices:
                messagebox.showwarning("Select batch", "Please select a batch to add.")
                return

            selected_batch = batch_list.get(selected_indices[0])
            self.queue_items.append(self._create_queue_item(selected_batch, "Waiting"))
            self._persist_wait_list()
            self._refresh_tree()
            
            # Reposition viewer focusing the newest member item
            added_batch_id = self.queue_items[-1]["id"]
            self.tree.selection_set(added_batch_id)
            self.tree.focus(added_batch_id)
            self.tree.see(added_batch_id)

        ttk.Button(dialog, text="Add", command=add_batch).grid(
            row=2, column=0, padx=(12, 6), pady=(6, 12), sticky="ew"
        )
        ttk.Button(dialog, text="Close", command=dialog.destroy).grid(
            row=2, column=1, padx=(6, 12), pady=(6, 12), sticky="ew"
        )
        self._center_window_on_screen(dialog)

    def remove_selected_batch(self) -> None:
        """Removes the highlighted task from the wait list, commits the update, and shifts focus to the adjacent item."""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Select batch", "Please select a batch to remove.")
            return

        selected_batch_id = selected[0]
        selected_index = next(
            (
                index
                for index, item in enumerate(self.queue_items)
                if item["id"] == selected_batch_id
            ),
            None,
        )
        if selected_index is None:
            return

        # Purge from primary memory container array structures
        self.queue_items.pop(selected_index)
        self._persist_wait_list()
        self._refresh_tree()

        if not self.queue_items:
            return

        # Maintain list selection index boundaries focus seamlessly post row adjustments
        next_index = min(selected_index, len(self.queue_items) - 1)
        next_batch_id = self.queue_items[next_index]["id"]
        self.tree.selection_set(next_batch_id)
        self.tree.focus(next_batch_id)
        self.tree.see(next_batch_id)

    def open_change_status_dialog(self) -> None:
        """Launches explicit status modal forms containing combobox selectors for standard workflow updates."""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Select batch", "Please select a batch to change status.")
            return

        selected_batch_id = selected[0]
        selected_item = next(
            (item for item in self.queue_items if item["id"] == selected_batch_id), None
        )
        if selected_item is None:
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Change status")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(dialog, text=f"Batch: {selected_item['batch_name']}").grid(
            row=0, column=0, padx=12, pady=(12, 6), sticky="w"
        )
        ttk.Label(dialog, text="Select new status:").grid(
            row=1, column=0, padx=12, pady=(0, 6), sticky="w"
        )

        selected_status = tk.StringVar(value=selected_item["status"])
        status_combo = ttk.Combobox(
            dialog,
            textvariable=selected_status,
            values=tuple(STATUSES),
            state="readonly",
            width=18,
        )
        status_combo.grid(row=2, column=0, padx=12, pady=6, sticky="ew")

        # Internal helper mapping execution status properties back into data models
        def update_status() -> None:
            self._set_batch_status(selected_batch_id, selected_status.get())
            dialog.destroy()

        ttk.Button(dialog, text="Update", command=update_status).grid(
            row=3, column=0, padx=12, pady=(6, 12), sticky="e"
        )

    def open_reorder_window(self) -> None:
        """Initializes a window to manually reorder active batch queues."""
        if self.account_type != "Admin":
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Reorder Batches")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        # Frame for the listbox
        list_frame = ttk.Frame(dialog, padding=(12, 12, 12, 12))
        list_frame.grid(row=0, column=0, sticky="nsew")

        # Listbox to show items
        # IMPORTANT: We use a monospaced font (Courier New) here so character widths are identical.
        # This is strictly required to make the right-alignment spacing work perfectly.
        listbox = tk.Listbox(list_frame, width=50, height=12, selectmode=tk.SINGLE, font=("Segoe UI", 10))
        listbox.grid(row=0, column=0, sticky="nsew")

        # Scrollbar integration
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")

        # Helper to rebuild the listbox so index numbers remain sequential and text aligns
        def refresh_listbox(selected_idx: int | None = None) -> None:
            listbox.delete(0, tk.END)
            for idx, item in enumerate(self.queue_items, start=1):
                left_text = f"{idx}. {item['batch_name']}"
                right_text = item['status']
                
                # f-string padding: 
                # :<35 forces the name block to be 35 characters wide (left-aligned)
                # :>12 forces the status block to be 12 characters wide (right-aligned)
                formatted_string = f"{left_text:<35} {right_text:>12}"
                listbox.insert(tk.END, formatted_string)
            
            # Restore selection highlight after redrawing
            if selected_idx is not None:
                listbox.selection_set(selected_idx)
                listbox.see(selected_idx)

        # Populate listbox initially
        refresh_listbox()

        # Frame for action buttons
        btn_frame = ttk.Frame(dialog, padding=(0, 12, 12, 12))
        btn_frame.grid(row=0, column=1, sticky="ns")

        def move_up() -> None:
            selection = listbox.curselection()
            if not selection or selection[0] == 0:
                return
            idx = selection[0]
            # Swap underlying data in the queue
            self.queue_items[idx], self.queue_items[idx - 1] = self.queue_items[idx - 1], self.queue_items[idx]
            refresh_listbox(idx - 1)

        def move_down() -> None:
            selection = listbox.curselection()
            if not selection or selection[0] == listbox.size() - 1:
                return
            idx = selection[0]
            # Swap underlying data in the queue
            self.queue_items[idx], self.queue_items[idx + 1] = self.queue_items[idx + 1], self.queue_items[idx]
            refresh_listbox(idx + 1)

        def save_order() -> None:
            # Silently apply Reindex Queue logic before saving to prevent ID divergence
            for index, item in enumerate(self.queue_items):
                item["id"] = f"queue-{index}"
            
            # Safely reset the sequence tracker to pick up cleanly from the new layout
            self._next_queue_id = len(self.queue_items)

            # Persist the beautifully formatted array to JSON and refresh the main UI
            self._persist_wait_list()
            self._refresh_tree()
            dialog.destroy()

        ttk.Button(btn_frame, text="Move Up", command=move_up).grid(row=0, column=0, pady=(0, 4), sticky="ew")
        ttk.Button(btn_frame, text="Move Down", command=move_down).grid(row=1, column=0, pady=(0, 12), sticky="ew")
        ttk.Button(btn_frame, text="Save Order", command=save_order).grid(row=2, column=0, sticky="ew")

        self._center_window_on_screen(dialog)

    def format_queue(self) -> None:
        """Re-indexes queue item IDs sequentially starting from 0 to prevent ID divergence."""
        if not self.queue_items:
            messagebox.showinfo("Reindex Queue Order", "Wait list is currently empty.")
            return
            
        for index, item in enumerate(self.queue_items):
            item["id"] = f"queue-{index}"
            
        # Safely reset the sequence tracker to pick up cleanly from the new layout
        self._next_queue_id = len(self.queue_items)
        
        self._persist_wait_list()
        self._refresh_tree()
        messagebox.showinfo("Reindex Queue Order", "Queue Order IDs reindexed sequentially.")

    def _set_context_status(self, status: str) -> None:
        """Targeting handler route linking item options menu prompts directly to background status queues."""
        if self._context_batch_id is None:
            return
        self._set_batch_status(self._context_batch_id, status)

    def _set_batch_status(self, batch_id: str, status: str) -> None:
        """Updates internal status properties, saves changes to disk, and updates visual rows."""
        selected_item = next(
            (item for item in self.queue_items if item["id"] == batch_id), None
        )
        if selected_item is None:
            return

        selected_item["status"] = status
        self._persist_wait_list()
        self._refresh_tree()
        
        # Retain element positioning contexts visual layout views post redraw processing
        self.tree.selection_set(batch_id)
        self.tree.focus(batch_id)
        self.tree.see(batch_id)

    def _on_tree_right_click(self, event: tk.Event) -> None:
        """Context menu dynamic popup framework handling row right-click events restricted to column #3."""
        row_id = self.tree.identify_row(event.y)
        column_id = self.tree.identify_column(event.x)
        
        # Click outside structural boundaries resets item focus
        if not row_id:
            self.tree.selection_remove(self.tree.selection())
            self.tree.focus("")
            return
        # Action is explicitly locked only to Column #3 (The Status parameter display field)
        if column_id != "#3":
            return

        self.tree.selection_set(row_id)
        self._context_batch_id = row_id # Buffer the context target node reference
        try:
            self.status_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.status_menu.grab_release()

    def _on_tree_left_click(self, event: tk.Event) -> None:
        """Resets active item context highlights if user clicks blank white space inside tree frames."""
        row_id = self.tree.identify_row(event.y)
        if row_id:
            return
        self.tree.selection_remove(self.tree.selection())
        self.tree.focus("")


    # =========================================================
    # --- 4. Account Management & Authentication ---
    # =========================================================

    def open_account_window(self) -> None:
        """Initializes account control panel containing operational details and shift parameters."""
        if self.account_window is not None and self.account_window.winfo_exists():
            self.account_window.lift()
            self.account_window.focus_force()
            return

        self.account_window = tk.Toplevel(self.root)
        self.account_window.title("Account")
        self.account_window.resizable(True, True)
        self.account_window.transient(self.root)
        self.account_window.columnconfigure(0, weight=1)
        self.account_window.rowconfigure(4, weight=1)
        self.account_window.protocol("WM_DELETE_WINDOW", self._close_account_window)

        self.account_name_var = tk.StringVar(value=self.account_name)
        self.account_type_var = tk.StringVar(value=self.account_type)

        ttk.Label(self.account_window, text="Account Name:").grid(
            row=0, column=0, padx=12, pady=(12, 4), sticky="w"
        )
        ttk.Label(self.account_window, textvariable=self.account_name_var).grid(
            row=1, column=0, padx=12, pady=(0, 8), sticky="w"
        )

        ttk.Label(self.account_window, text="Account Type:").grid(
            row=2, column=0, padx=12, pady=(0, 4), sticky="w"
        )
        ttk.Label(self.account_window, textvariable=self.account_type_var).grid(
            row=3, column=0, padx=12, pady=(0, 12), sticky="w"
        )

        change_frame = ttk.LabelFrame(self.account_window, text="Change account", padding=8)
        change_frame.grid(row=4, column=0, padx=12, pady=(0, 12), sticky="ew")
        change_frame.columnconfigure(0, weight=1)

        ttk.Button(
            change_frame,
            text="Change Account",
            command=self.open_login_window,
        ).grid(row=0, column=0, pady=(2, 0), sticky="e")
        self._center_window_on_screen(self.account_window)

    def _apply_account(self, account_key: str) -> None:
        """Updates internal authentication state definitions when swapping active profiles."""
        if account_key not in ACCOUNTS:
            return

        self.account_key = account_key
        selected_account = ACCOUNTS[account_key]
        self.account_name = selected_account["username"]
        self.account_type = selected_account["account_type"]
        
        # Propagate changes to tracked control UI string bindings
        if self.account_name_var is not None:
            self.account_name_var.set(self.account_name)
        if self.account_type_var is not None:
            self.account_type_var.set(self.account_type)
        if self.account_type_menu_var is not None:
            self.account_type_menu_var.set(self.account_type)
            
        # Revise access policies matching configuration updates
        self._update_options_menu_access()
        
        # Force terminate unauthorized panel workspaces if dropping out of Admin status
        if self.account_type != "Admin":
            self._close_manage_accounts_window()
            self._close_batch_editor_window()

    def _update_options_menu_access(self) -> None:
        """Enforces role boundaries by adjusting activation structures on restricted widgets."""
        if self.options_menu is None:
            return
        is_admin = self.account_type == "Admin"
        batches_state = tk.NORMAL if is_admin else tk.DISABLED
        
        # Set state flags inside dropdown elements and dedicated action buttons
        self.options_menu.entryconfigure("Batches", state=batches_state)
        if self.manage_accounts_button is not None:
            self.manage_accounts_button.configure(state=tk.NORMAL if is_admin else tk.DISABLED)

    def open_login_window(self) -> None:
        """Initializes user authentication form frame allowing context state profile shifting."""
        if self.login_window is not None and self.login_window.winfo_exists():
            self.login_window.lift()
            self.login_window.focus_force()
            return

        self.login_window = tk.Toplevel(self.root)
        self.login_window.title("Login")
        self.login_window.resizable(False, False)
        self.login_window.transient(self.root)
        self.login_window.columnconfigure(1, weight=1)
        self.login_window.protocol("WM_DELETE_WINDOW", self._close_login_window)

        ttk.Label(self.login_window, text="Username:").grid(
            row=0, column=0, padx=(12, 6), pady=(12, 6), sticky="w"
        )
        username_var = tk.StringVar()
        username_entry = ttk.Entry(self.login_window, textvariable=username_var, width=24)
        username_entry.grid(row=0, column=1, padx=(0, 12), pady=(12, 6), sticky="ew")

        ttk.Label(self.login_window, text="Password:").grid(
            row=1, column=0, padx=(12, 6), pady=6, sticky="w"
        )
        password_var = tk.StringVar()
        password_entry = ttk.Entry(self.login_window, textvariable=password_var, show="*", width=24)
        password_entry.grid(row=1, column=1, padx=(0, 12), pady=6, sticky="ew")

        error_label = tk.Label(self.login_window, text="", fg="red")
        error_label.grid(row=2, column=0, columnspan=2, padx=12, pady=(0, 6), sticky="w")

        # Handles verification processing when submitting login credentials
        def login() -> None:
            username = username_var.get().strip()
            password = password_var.get()
            matched_account_key = next(
                (
                    key
                    for key, details in ACCOUNTS.items()
                    if details["username"] == username and details["password"] == password
                ),
                None,
            )
            if matched_account_key is None:
                error_label.configure(text="Invalid Username or Password .")
                return

            self._apply_account(matched_account_key)
            self._close_account_window()

        # Keyboard accessibility shortcuts
        def move_focus_to_password(_event: tk.Event) -> str:
            password_entry.focus_set()
            return "break"

        def submit_login(_event: tk.Event) -> str:
            login()
            return "break"

        ttk.Button(self.login_window, text="Login", command=login).grid(
            row=3, column=0, columnspan=2, padx=12, pady=(0, 12), sticky="e"
        )

        username_entry.bind("<Return>", move_focus_to_password)
        password_entry.bind("<Return>", submit_login)
        username_entry.focus_set()
        self._center_window_on_screen(self.login_window)

    def open_manage_accounts_window(self) -> None:
        """Launches credential configuration portal dashboard. Restricted exclusively to Admins."""
        if self.account_type != "Admin":
            return
        if self.manage_accounts_window is not None and self.manage_accounts_window.winfo_exists():
            self.manage_accounts_window.lift()
            self.manage_accounts_window.focus_force()
            return

        self.manage_accounts_window = tk.Toplevel(self.root)
        self.manage_accounts_window.title("Manage Accounts")
        self.manage_accounts_window.resizable(True, True)
        self.manage_accounts_window.transient(self.root)
        self.manage_accounts_window.columnconfigure(0, weight=1)
        self.manage_accounts_window.rowconfigure(0, weight=1)
        self.manage_accounts_window.protocol("WM_DELETE_WINDOW", self._close_manage_accounts_window)

        tree_frame = ttk.Frame(self.manage_accounts_window, padding=(12, 12, 12, 8))
        tree_frame.grid(row=0, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        self.accounts_tree = ttk.Treeview(
            tree_frame, columns=("username", "account_type"), show="headings", height=8
        )
        self.accounts_tree.heading("username", text="Username")
        self.accounts_tree.heading("account_type", text="Account Type")
        self.accounts_tree.column("username", width=180, anchor="w")
        self.accounts_tree.column("account_type", width=120, anchor="center")
        self.accounts_tree.grid(row=0, column=0, sticky="nsew")

        accounts_scroll = ttk.Scrollbar(
            tree_frame, orient="vertical", command=self.accounts_tree.yview
        )
        self.accounts_tree.configure(yscrollcommand=accounts_scroll.set)
        accounts_scroll.grid(row=0, column=1, sticky="ns")

        form_frame = ttk.LabelFrame(
            self.manage_accounts_window, text="Create account", padding=8
        )
        form_frame.grid(row=1, column=0, padx=12, pady=(0, 8), sticky="ew")
        form_frame.columnconfigure(1, weight=1)

        ttk.Label(form_frame, text="Username:").grid(
            row=0, column=0, padx=(0, 6), pady=(0, 6), sticky="w"
        )
        username_var = tk.StringVar()
        username_entry = ttk.Entry(form_frame, textvariable=username_var)
        username_entry.grid(row=0, column=1, pady=(0, 6), sticky="ew")

        ttk.Label(form_frame, text="Password:").grid(
            row=1, column=0, padx=(0, 6), pady=(0, 6), sticky="w"
        )
        password_var = tk.StringVar()
        password_entry = ttk.Entry(form_frame, textvariable=password_var, show="*")
        password_entry.grid(row=1, column=1, pady=(0, 6), sticky="ew")

        ttk.Label(form_frame, text="Account Type:").grid(
            row=2, column=0, padx=(0, 6), pady=(0, 6), sticky="w"
        )
        account_type_var = tk.StringVar(value="Operator")
        account_type_combo = ttk.Combobox(
            form_frame, textvariable=account_type_var, values=ACCOUNT_TYPES, state="readonly"
        )
        account_type_combo.grid(row=2, column=1, pady=(0, 6), sticky="ew")

        buttons_frame = ttk.Frame(self.manage_accounts_window, padding=(12, 0, 12, 12))
        buttons_frame.grid(row=2, column=0, sticky="ew")
        buttons_frame.columnconfigure((0, 1, 2), weight=1)

        # Logic routine processing creation validation pipelines
        def create_account() -> None:
            username = username_var.get().strip()
            password = password_var.get()
            account_type = account_type_var.get()

            if not username:
                messagebox.showwarning("Invalid data", "Username is required.")
                return
            if not password:
                messagebox.showwarning("Invalid data", "Password is required.")
                return
            if account_type not in ACCOUNT_TYPES:
                messagebox.showwarning("Invalid data", "Account Type must be Admin or Operator.")
                return
            if username in ACCOUNTS:
                messagebox.showwarning("Duplicate user", "Username already exists.")
                return
            # Security verification step required prior to creating a new administrator account
            if account_type == "Admin" and not self._confirm_current_admin_password():
                return

            ACCOUNTS[username] = {
                "account_type": account_type,
                "username": username,
                "password": password,
            }
            save_accounts(ACCOUNTS)
            self._refresh_accounts_tree()
            self.accounts_tree.selection_set(username)
            self.accounts_tree.focus(username)
            self.accounts_tree.see(username)
            username_var.set("")
            password_var.set("")
            account_type_var.set("Operator")
            username_entry.focus_set()

        # Logic handler deleting records with integrated security double-checks
        def delete_account() -> None:
            selected = self.accounts_tree.selection()
            if not selected:
                messagebox.showwarning("Select account", "Please select an account to delete.")
                return

            username = selected[0]
            account = ACCOUNTS.get(username)
            if account is None:
                return
            # Self-deletion block safeguard
            if username == self.account_key:
                messagebox.showwarning(
                    "Cannot delete", "You cannot delete the account currently logged in."
                )
                return
            if not self._confirm_account_deletion():
                return
            # Security gate needing operational password re-verification prior to purging an admin account
            if account["account_type"] == "Admin" and not self._confirm_current_admin_password():
                return

            del ACCOUNTS[username]
            save_accounts(ACCOUNTS)
            self._refresh_accounts_tree()

        ttk.Button(buttons_frame, text="Create", command=create_account).grid(
            row=0, column=0, padx=(0, 6), sticky="ew"
        )
        ttk.Button(buttons_frame, text="Delete", command=delete_account).grid(
            row=0, column=1, padx=6, sticky="ew"
        )
        ttk.Button(
            buttons_frame, text="Close", command=self._close_manage_accounts_window
        ).grid(row=0, column=2, padx=(6, 0), sticky="ew")

        self._refresh_accounts_tree()
        username_entry.focus_set()
        self._center_window_on_screen(self.manage_accounts_window)

    def _refresh_accounts_tree(self) -> None:
        """Populates account treeview sorted primarily by Admin role first, then alphabetically."""
        if self.accounts_tree is None:
            return
        self.accounts_tree.delete(*self.accounts_tree.get_children())
        ordered_usernames = sorted(
            ACCOUNTS.keys(),
            key=lambda username: (
                ACCOUNTS[username]["account_type"] != "Admin",
                username.lower(),
            ),
        )
        for username in ordered_usernames:
            account = ACCOUNTS[username]
            self.accounts_tree.insert(
                "", "end", iid=username, values=(account["username"], account["account_type"])
            )

    def _confirm_current_admin_password(self) -> bool:
        """Prompts for current admin user password to verify access before dangerous edits."""
        provided_password = simpledialog.askstring(
            "Password Confirmation",
            "Please enter your password:",
            parent=self.manage_accounts_window,
            show="*",
        )
        if provided_password is None:
            return False

        current_account = ACCOUNTS.get(self.account_key)
        if current_account is None or current_account["password"] != provided_password:
            messagebox.showerror("Authentication failed", "Incorrect password.")
            return False
        return True

    def _confirm_account_deletion(self) -> bool:
        """Launches explicitly designed, non-native confirmation modal windows for row removal checks."""
        result = {"confirmed": False}
        dialog = tk.Toplevel(self.manage_accounts_window if self.manage_accounts_window else self.root)
        dialog.title("Delete account")
        dialog.resizable(False, False)
        dialog.transient(self.manage_accounts_window if self.manage_accounts_window else self.root)
        dialog.grab_set() # Block interaction with background frames
        dialog.columnconfigure((0, 1), weight=1)

        ttk.Label(dialog, text="Are you sure you want to delete this account?").grid(
            row=0, column=0, columnspan=2, padx=12, pady=(12, 10), sticky="w"
        )

        def confirm_delete() -> None:
            result["confirmed"] = True
            dialog.destroy()

        ttk.Button(dialog, text="I am sure", command=confirm_delete).grid(
            row=1, column=0, padx=(12, 6), pady=(0, 12), sticky="ew"
        )
        ttk.Button(dialog, text="Close", command=dialog.destroy).grid(
            row=1, column=1, padx=(6, 12), pady=(0, 12), sticky="ew"
        )

        self._center_window_on_screen(dialog)
        dialog.wait_window()
        return result["confirmed"]


    # =========================================================
    # --- 5. Batch Types Editor (Admin Only) ---
    # =========================================================

    def open_batches_window(self) -> None:
        """Initializes batch design manager settings. Restricted exclusively to Admins."""
        if self.account_type != "Admin":
            return
        if self.batch_editor_window is not None and self.batch_editor_window.winfo_exists():
            self.batch_editor_window.lift()
            self.batch_editor_window.focus_force()
            return

        self.batch_editor_window = tk.Toplevel(self.root)
        self.batch_editor_window.title("Batch Editor")
        self.batch_editor_window.resizable(True, True)
        self.batch_editor_window.transient(self.root)
        self.batch_editor_window.columnconfigure(0, weight=1)
        self.batch_editor_window.rowconfigure(0, weight=1)
        self.batch_editor_window.protocol("WM_DELETE_WINDOW", self._close_batch_editor_window)

        list_frame = ttk.Frame(self.batch_editor_window, padding=(12, 12, 12, 8))
        list_frame.grid(row=0, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self.batch_types_listbox = tk.Listbox(list_frame, exportselection=False, height=10)
        self.batch_types_listbox.grid(row=0, column=0, sticky="nsew")
        list_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.batch_types_listbox.yview)
        self.batch_types_listbox.configure(yscrollcommand=list_scroll.set)
        list_scroll.grid(row=0, column=1, sticky="ns")

        create_frame = ttk.LabelFrame(self.batch_editor_window, text="Create batch type", padding=8)
        create_frame.grid(row=1, column=0, padx=12, pady=(0, 8), sticky="ew")
        create_frame.columnconfigure(1, weight=1)

        ttk.Label(create_frame, text="Batch type name:").grid(
            row=0, column=0, padx=(0, 6), pady=(0, 6), sticky="w"
        )
        new_batch_type_var = tk.StringVar()
        new_batch_type_entry = ttk.Entry(create_frame, textvariable=new_batch_type_var)
        new_batch_type_entry.grid(row=0, column=1, pady=(0, 6), sticky="ew")

        buttons_frame = ttk.Frame(self.batch_editor_window, padding=(12, 0, 12, 12))
        buttons_frame.grid(row=2, column=0, sticky="ew")
        buttons_frame.columnconfigure((0, 1, 2), weight=1)

        # Inner helper routine processing item generation entries
        def create_batch_type() -> None:
            batch_type = new_batch_type_var.get().strip()
            if not batch_type:
                messagebox.showwarning("Invalid batch type", "Batch type name is required.")
                return
            # Duplicate prevention check (case-insensitive)
            if any(existing.lower() == batch_type.lower() for existing in self.batch_types):
                messagebox.showwarning("Duplicate batch type", "This batch type already exists.")
                return

            self.batch_types.append(batch_type)
            self._persist_batch_types()
            self._refresh_batch_types_listbox(selected=batch_type)
            new_batch_type_var.set("")
            new_batch_type_entry.focus_set()

        # Inner helper routine handling category deletion operations
        def delete_batch_type() -> None:
            if self.batch_types_listbox is None:
                return
            selected = self.batch_types_listbox.curselection()
            if not selected:
                messagebox.showwarning(
                    "Select batch type", "Please select a batch type to remove."
                )
                return

            selected_batch_type = self.batch_types_listbox.get(selected[0])
            self.batch_types = [
                batch_type for batch_type in self.batch_types if batch_type != selected_batch_type
            ]
            self._persist_batch_types()
            self._refresh_batch_types_listbox()

        ttk.Button(buttons_frame, text="Create", command=create_batch_type).grid(
            row=0, column=0, padx=(0, 6), sticky="ew"
        )
        ttk.Button(buttons_frame, text="Remove", command=delete_batch_type).grid(
            row=0, column=1, padx=6, sticky="ew"
        )
        ttk.Button(buttons_frame, text="Close", command=self._close_batch_editor_window).grid(
            row=0, column=2, padx=(6, 0), sticky="ew"
        )

        self._refresh_batch_types_listbox()
        new_batch_type_entry.focus_set()
        self._center_window_on_screen(self.batch_editor_window)

    def _refresh_batch_types_listbox(self, selected: str | None = None) -> None:
        """Re-renders batch category types and maintains item selections if provided."""
        if self.batch_types_listbox is None:
            return
        self.batch_types_listbox.delete(0, tk.END)
        for batch_type in self.batch_types:
            self.batch_types_listbox.insert(tk.END, batch_type)

        if not self.batch_types:
            return

        if selected is not None and selected in self.batch_types:
            selected_index = self.batch_types.index(selected)
        else:
            selected_index = 0
        self.batch_types_listbox.selection_set(selected_index)
        self.batch_types_listbox.see(selected_index)

    def _persist_batch_types(self) -> None:
        """Sorts categories alphabetically and updates background JSON datastores."""
        self.batch_types = sorted(self.batch_types, key=str.lower)
        BATCH_TYPES[:] = self.batch_types # Sync reference array modifications
        save_batch_types(self.batch_types)


    # =========================================================
    # --- 6. Window Cleanup Routines ---
    # =========================================================

    def _close_account_window(self) -> None:
        self._close_login_window()
        if self.account_window is None:
            return
        self.account_window.destroy()
        self.account_window = None
        self.account_name_var = None
        self.account_type_var = None

    def _close_login_window(self) -> None:
        if self.login_window is None:
            return
        self.login_window.destroy()
        self.login_window = None

    def _close_manage_accounts_window(self) -> None:
        if self.manage_accounts_window is None:
            return
        self.manage_accounts_window.destroy()
        self.manage_accounts_window = None
        self.accounts_tree = None

    def _close_batch_editor_window(self) -> None:
        if self.batch_editor_window is None:
            return
        self.batch_editor_window.destroy()
        self.batch_editor_window = None
        self.batch_types_listbox = None


def main() -> None:
    """Application orchestration method bootstrapping Tkinter components and main loop lifecycles."""
    root = tk.Tk()
    BatchQueueApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()